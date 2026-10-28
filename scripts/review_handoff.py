"""Render an unknown-outcome review from the worker's ORIGINAL report readback.

No ledger merge, native send, model call, new report schema or outcome prediction.
The report owns its request/result fields; pointers adapt its existing schema.
"""
import hashlib
import math
from pathlib import Path

from return_receipt import (ReceiptError, MAX_BYTES, canonical, _keys, _load,
                            _pointer, _sha, _text)


def _decode(raw):
    try:
        value = _load(raw)
    except RecursionError as exc:
        raise ReceiptError("JSON depth") from exc
    stack, count = [(value, 0)], 0
    while stack:
        item, depth = stack.pop()
        count += 1
        if depth > 20 or count > 12000:
            raise ReceiptError("JSON bounds")
        if isinstance(item, dict):
            stack.extend((v, depth + 1) for v in item.values())
        elif isinstance(item, list):
            stack.extend((v, depth + 1) for v in item)
        elif isinstance(item, float) and not math.isfinite(item):
            raise ReceiptError("non-finite JSON number")
    return value


def _request(r):
    _keys(r, ("sender_thread", "target_thread", "request_id", "payload_utf8_sha256"), "request")
    for k in ("sender_thread", "target_thread", "request_id"):
        _text(r[k], k)
    _sha(r["payload_utf8_sha256"], "payload")


def _subjects(rows):
    if not isinstance(rows, list) or not 1 <= len(rows) <= 16:
        raise ReceiptError("subjects: invalid array")
    seen = set()
    for row in rows:
        _keys(row, ("object_key", "ref", "sha256"), "subject")
        _text(row["object_key"], "subject key")
        _text(row["ref"], "subject ref")
        _sha(row["sha256"], "subject sha")
        if row["object_key"] in seen:
            raise ReceiptError("duplicate subject")
        seen.add(row["object_key"])


def parse_review_dispatch(prompt):
    if not isinstance(prompt, str):
        raise ReceiptError("dispatch: invalid string")
    try:
        encoded = prompt.encode("utf-8")
    except UnicodeError as exc:
        raise ReceiptError("dispatch: invalid unicode") from exc
    if len(encoded) > MAX_BYTES:
        raise ReceiptError("dispatch: too large or invalid")
    packet = _decode(prompt)
    _keys(packet, ("format", "payload", "contract"), "dispatch")
    if packet["format"] != "review-dispatch-v1" or canonical(packet) != prompt:
        raise ReceiptError("dispatch: noncanonical")
    _text(packet["payload"], "payload")
    c = packet["contract"]
    _keys(c, ("request", "subjects", "questions", "supersedes"), "contract")
    _request(c["request"])
    if hashlib.sha256(packet["payload"].encode()).hexdigest() != c["request"]["payload_utf8_sha256"]:
        raise ReceiptError("payload fingerprint")
    _subjects(c["subjects"])
    questions = c["questions"]
    if not isinstance(questions, list) or not 1 <= len(questions) <= 64:
        raise ReceiptError("questions: invalid array")
    seen, count = set(), 0
    for q in questions:
        _keys(q, ("question_key", "text", "object_keys"), "question")
        _text(q["question_key"], "question key")
        _text(q["text"], "question text")
        if q["question_key"] in seen:
            raise ReceiptError("duplicate question")
        seen.add(q["question_key"])
        rows = q["object_keys"]
        if not isinstance(rows, list) or not 1 <= len(rows) <= 16:
            raise ReceiptError("question subjects: invalid array")
        for k in rows:
            _text(k, "question subject")
        if len(set(rows)) != len(rows) or any(k not in {s["object_key"] for s in c["subjects"]} for k in rows):
            raise ReceiptError("question subjects mismatch")
        count += len(rows)
    if count > 128:
        raise ReceiptError("too many obligations")
    if c["supersedes"] is not None:
        old, r = c["supersedes"], c["request"]
        _request(old)
        if (old["sender_thread"] != r["sender_thread"] or old["target_thread"] != r["target_thread"] or
                old["request_id"] == r["request_id"]):
            raise ReceiptError("revision must name own different request")
    return packet


def _result(result, contract, evidence_readbacks):
    _keys(result, ("subjects", "answers", "limitations", "next_scope"), "result")
    _subjects(result["subjects"])
    if result["subjects"] != contract["subjects"]:
        raise ReceiptError("report input subjects mismatch")
    rows = result["answers"]
    if not isinstance(rows, list) or len(rows) > 128:
        raise ReceiptError("answers: invalid array")
    obligations = {(k, q["question_key"]) for q in contract["questions"] for k in q["object_keys"]}
    seen = set()
    if not isinstance(evidence_readbacks, dict):
        raise ReceiptError("evidence readbacks required")
    for a in rows:
        _keys(a, ("object_key", "question_key", "status", "answer", "evidence"), "answer")
        for k in ("object_key", "question_key", "status", "answer"):
            _text(a[k], "answer " + k)
        pair = (a["object_key"], a["question_key"])
        if pair in seen or pair not in obligations:
            raise ReceiptError("duplicate or unrequested answer")
        seen.add(pair)
        if a["status"] not in ("completed", "failed", "limited", "blocked", "not_run", "unknown"):
            raise ReceiptError("answer status")
        if not isinstance(a["evidence"], list) or len(a["evidence"]) > 16:
            raise ReceiptError("answer evidence array")
        if a["status"] in ("completed", "failed") and not a["evidence"]:
            raise ReceiptError("completed answer lacks evidence")
        for e in a["evidence"]:
            _keys(e, ("ref", "sha256"), "evidence")
            _text(e["ref"], "evidence ref")
            _sha(e["sha256"], "evidence sha")
            raw = evidence_readbacks.get(e["ref"])
            if not isinstance(raw, bytes) or len(raw) > MAX_BYTES:
                raise ReceiptError("evidence readback unavailable or too large")
            # This transport is UTF-8; binary/large evidence stays in the original
            # domain review and cannot be mechanically accepted by this adapter.
            try:
                raw.decode("utf-8", errors="strict")
            except UnicodeError as exc:
                raise ReceiptError("evidence readback is not UTF-8") from exc
            if hashlib.sha256(raw).hexdigest() != e["sha256"]:
                raise ReceiptError("evidence reference/fingerprint mismatch")
    limits = result["limitations"]
    if not isinstance(limits, list) or not 1 <= len(limits) <= 32:
        raise ReceiptError("explicit limitations required")
    for limit in limits:
        _text(limit, "limitation")
    _text(result["next_scope"], "next scope")


def review_source(prompt, report_ref, report_readback, *, request_pointer,
                  result_pointer, evidence_readbacks):
    """Return a locator from the original report readback; no writes or sending.

    Missing/not_run answers are legal and remain partial at the receiver. Neither
    producer nor receiver confers substantive acceptance from report self-labels.
    All evidence bytes are explicit original readbacks, never auto-followed paths.
    """
    c = parse_review_dispatch(prompt)["contract"]
    _text(report_ref, "report ref")
    if not isinstance(report_readback, bytes) or len(report_readback) > MAX_BYTES:
        raise ReceiptError("report readback unavailable or too large")
    try:
        if Path(report_ref).read_bytes() != report_readback:
            raise ReceiptError("original report changed")
    except OSError as exc:
        raise ReceiptError("original report unavailable") from exc
    report = _decode(report_readback)
    if _pointer(report, request_pointer) != c["request"]:
        raise ReceiptError("report original request mismatch")
    result = _pointer(report, result_pointer)
    _result(result, c, evidence_readbacks)
    return {"request": c["request"],
        "report": {"ref": report_ref, "sha256": hashlib.sha256(report_readback).hexdigest(),
                   "request_pointer": request_pointer, "result_pointer": result_pointer}}


def produce_review(prompt, report_ref, report_readback, *, request_pointer,
                   result_pointer, evidence_readbacks):
    """Compatibility for already issued review-return-v1 requests only.

    New callers retain review_source() in their original result record and let
    the human-facing final describe the result. Neither path grants permission
    to expose the report, evidence or unrelated fields to a model or another chat.
    """
    source = review_source(prompt, report_ref, report_readback,
                           request_pointer=request_pointer, result_pointer=result_pointer,
                           evidence_readbacks=evidence_readbacks)
    body = canonical({"format": "review-return-v1", **source,
                      "result": _pointer(_decode(report_readback), result_pointer)})
    if len(body.encode()) > MAX_BYTES:
        raise ReceiptError("final too large")
    return body
