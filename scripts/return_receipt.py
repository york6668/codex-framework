"""Finite result rendering for an existing merge/readback operation.

This module neither merges a ledger nor sends/accepts a native final. The original
producer calls produce() with its committed readback. The original coordinator
uses the separately shipped pure-JS receiver on actual native final text.
No native source, authorization, report semantics or future invocation is inferred.
"""
import hashlib
import json
import re
from pathlib import Path

FORMAT = "finite-return-v2"
MAX_BYTES = 64 * 1024


class ReceiptError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def _keys(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ReceiptError(label + ": invalid fields")


def _text(value, label):
    if not isinstance(value, str) or not value.strip() or len(value) > 16000:
        raise ReceiptError(label + ": invalid string")
    if any(0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise ReceiptError(label + ": invalid unicode")


def _sha(value, label):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ReceiptError(label + ": invalid sha256")


def validate_expected(value):
    _keys(value, ("request", "results"), "expected")
    req = value["request"]
    _keys(req, ("sender_thread", "target_thread", "request_id", "payload_utf8_sha256"), "request")
    for key in ("sender_thread", "target_thread", "request_id"):
        _text(req[key], "request." + key)
    _sha(req["payload_utf8_sha256"], "request.payload_utf8_sha256")
    rows = value["results"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= 16:
        raise ReceiptError("results: invalid array")
    seen = set()
    for row in rows:
        _keys(row, ("object_key", "report_ref", "report_sha256", "identities",
                    "outcome", "summary", "limitations", "next_scope"), "result")
        for key in ("object_key", "report_ref", "summary", "next_scope"):
            _text(row[key], "result." + key)
        _sha(row["report_sha256"], "result.report_sha256")
        if row["object_key"] in seen:
            raise ReceiptError("results: duplicate object")
        seen.add(row["object_key"])
        if row["outcome"] not in ("passed", "failed", "not_run", "limited"):
            raise ReceiptError("result.outcome: invalid")
        if not isinstance(row["limitations"], list) or not row["limitations"]:
            raise ReceiptError("result.limitations: explicit array required")
        for v in row["limitations"]:
            _text(v, "result.limitation")
        if not isinstance(row["identities"], list):
            raise ReceiptError("result.identities: invalid array")
        names = set()
        for item in row["identities"]:
            _keys(item, ("name", "value"), "identity")
            for key in ("name", "value"):
                _text(item[key], "identity." + key)
            if item["name"] in names:
                raise ReceiptError("identity: duplicate name")
            names.add(item["name"])
    if len(canonical(value).encode()) > MAX_BYTES:
        raise ReceiptError("expected: too large")
    return value


def render(expected, ledger_sha256):
    validate_expected(expected)
    _sha(ledger_sha256, "ledger_sha256")
    body = canonical(dict(format=FORMAT, request=expected["request"],
                          results=expected["results"], ledger_after_sha256=ledger_sha256))
    if len(body.encode()) > MAX_BYTES:
        raise ReceiptError("final: too large")
    return body


def _pointer(value, pointer):
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ReceiptError("binding: invalid JSON pointer")
    for token in pointer[1:].split("/"):
        if re.search(r"~(?![01])", token):
            raise ReceiptError("binding: invalid escape")
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict) and token in value:
            value = value[token]
        elif isinstance(value, list) and len(token) <= 10 and re.fullmatch(r"0|[1-9][0-9]*", token):
            index = int(token)
            if index >= len(value):
                raise ReceiptError("binding: missing index")
            value = value[index]
        else:
            raise ReceiptError("binding: missing field")
    return value


def _load(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ReceiptError("JSON: duplicate field")
            value[key] = item
        return value
    def bad(_):
        raise ReceiptError("JSON: non-finite number")
    try:
        return json.loads(raw, object_pairs_hook=unique, parse_constant=bad)
    except (TypeError, ValueError, UnicodeError) as exc:
        if isinstance(exc, ReceiptError):
            raise
        raise ReceiptError("JSON: malformed") from exc


def produce(expected, committed_readback, bindings, *, payload):
    """Render from the same successful committed readback, without any writes.

    bindings is local to the producer, not required from the parent. Every result
    has a ledger receipt projection and a next-scope projection pinned by its
    original domain owner. Reports retain their own distinct identities/statuses.
    The parent pre-pins expected's complete finite meaning from the original
    reports/delegation; this utility does not derive arbitrary prose semantics.
    """
    validate_expected(expected)
    _text(payload, "logical payload")
    if hashlib.sha256(payload.encode("utf-8")).hexdigest() != expected["request"]["payload_utf8_sha256"]:
        raise ReceiptError("logical payload fingerprint mismatch")
    if not isinstance(committed_readback, bytes):
        raise ReceiptError("committed_readback: bytes required")
    ledger = _load(committed_readback)
    if not isinstance(bindings, list) or len(bindings) != len(expected["results"]):
        raise ReceiptError("bindings: one per result required")
    seen = set()
    for binding in bindings:
        _keys(binding, ("object_key", "receipt_pointer", "receipt_value", "report_pointer",
                        "next_pointer", "next_value"), "binding")
        key = binding["object_key"]
        _text(key, "binding.object_key")
        if key in seen:
            raise ReceiptError("bindings: duplicate object")
        seen.add(key)
        if key not in {r["object_key"] for r in expected["results"]}:
            raise ReceiptError("bindings: unexpected object")
        for prefix in ("receipt", "next"):
            actual = _pointer(ledger, binding[prefix + "_pointer"])
            if canonical(actual) != canonical(binding[prefix + "_value"]):
                raise ReceiptError("committed " + prefix + " projection mismatch")
        row = next(r for r in expected["results"] if r["object_key"] == key)
        report_pointer = binding["report_pointer"]
        if (not isinstance(report_pointer, str) or
                not report_pointer.startswith(binding["receipt_pointer"] + "/")):
            raise ReceiptError("report: must belong to selected receipt projection")
        if _pointer(ledger, report_pointer) != {"path": row["report_ref"],
                                              "sha256": row["report_sha256"]}:
            raise ReceiptError("report: committed identity mismatch")
        try:
            raw = Path(row["report_ref"]).read_bytes()
        except OSError as exc:
            raise ReceiptError("report: unavailable") from exc
        if hashlib.sha256(raw).hexdigest() != row["report_sha256"]:
            raise ReceiptError("report: fingerprint mismatch")
        _load(raw)  # Original report is JSON; do not alter its domain schema.
    return render(expected, hashlib.sha256(committed_readback).hexdigest())


def parse_dispatch(prompt):
    """Read the exact sender logical packet, never a provider/FCO wrapper."""
    if not isinstance(prompt, str):
        raise ReceiptError("dispatch: invalid string")
    try:
        size = len(prompt.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ReceiptError("dispatch: invalid unicode") from exc
    if size > MAX_BYTES:
        raise ReceiptError("dispatch: too large")
    packet = _load(prompt)
    _keys(packet, ("format", "payload", "expected"), "dispatch")
    if packet["format"] != "finite-dispatch-v1" or canonical(packet) != prompt:
        raise ReceiptError("dispatch: noncanonical logical packet")
    _text(packet["payload"], "logical payload")
    validate_expected(packet["expected"])
    if hashlib.sha256(packet["payload"].encode("utf-8")).hexdigest() != packet["expected"]["request"]["payload_utf8_sha256"]:
        raise ReceiptError("dispatch: logical payload fingerprint mismatch")
    return packet
