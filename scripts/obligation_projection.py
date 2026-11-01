"""Read selected local references without deciding authorization or task readiness.

Only the explicit document is opened. Frontier projection returns intact values;
--read provides a bounded, metadata-first view of one explicitly selected value.
Neither mode touches other files, native tasks, messages or persistent state.
"""
import argparse
import copy
import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import quote, unquote_to_bytes


DEFAULT_FAMILIES = (
    "first_ready_unassigned", "assigned_inflight_do_not_duplicate",
    "current_real_backlog_action", "current_authorization_propagation_rootcause",
)
READ_INPUT_BYTES = 8 * 1024 * 1024
READ_OUTPUT_BYTES = 16 * 1024
READ_TEXT_CHARS = 1000


class ProjectionError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def pointer(tokens):
    return "".join("/" + str(t).replace("~", "~0").replace("/", "~1") for t in tokens)


def fragment(path):
    return "#" + quote(path, safe="/~")


def resolve(document, ref):
    """Return a value and its exact ordinary JSON pointer, including selectors.

    RFC 6901 pointers/fragments are supported. A missing literal object key may
    instead select key[id=exact-string] from an array, requiring a unique match.
    Literal keys take precedence; a numeric pointer also disambiguates them.
    """
    if not isinstance(ref, str):
        raise ProjectionError("invalid_ref", "reference must be a string")
    if ref.startswith("#"):
        if re.search(r"%(?![0-9a-fA-F]{2})", ref):
            raise ProjectionError("invalid_escape", "invalid fragment percent escape")
        try:
            ref = unquote_to_bytes(ref[1:]).decode("utf-8")
        except UnicodeError as exc:
            raise ProjectionError("invalid_escape", "fragment is not UTF-8") from exc
    if ref and not ref.startswith("/"):
        raise ProjectionError("nonlocal_ref", "only local JSON pointers are supported")
    value, actual = document, []
    for token in ref[1:].split("/") if ref else []:
        if re.search(r"~(?![01])", token):
            raise ProjectionError("invalid_escape", "invalid JSON pointer escape")
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict):
            if token in value:
                value = value[token]
                actual.append(token)
                continue
            match = re.fullmatch(r"([^\[\]]+)\[id=([^\[\]]+)\]", token)
            if match and isinstance(value.get(match[1]), list):
                items = value[match[1]]
                matches = [i for i, row in enumerate(items)
                           if isinstance(row, dict) and row.get("id") == match[2]]
                if len(matches) != 1:
                    code = "ambiguous_id" if matches else "missing_id"
                    raise ProjectionError(code, "selector requires exactly one matching string id")
                actual.extend((match[1], str(matches[0])))
                value = items[matches[0]]
                continue
            raise ProjectionError("missing_field", "reference does not name an existing field")
        if isinstance(value, list):
            if not re.fullmatch(r"0|[1-9][0-9]*", token):
                raise ProjectionError("invalid_index", "array index must be a nonnegative canonical integer")
            if len(token) > len(str(len(value))) or int(token) >= len(value):
                raise ProjectionError("missing_index", "array index is outside the document")
            actual.append(token)
            value = value[int(token)]
            continue
        raise ProjectionError("noncontainer", "reference descends through a scalar")
    return value, pointer(actual)


def project(document, frontier_ref, families=None):
    """Expand both forms of each selected reference family, retaining origins.

    A bad field/reference is local to that entry. Resolved aliases deduplicate by
    actual pointer, never by status, payload similarity, thread or latest turn.
    """
    frontier, location = resolve(document, frontier_ref)
    if not isinstance(frontier, dict):
        raise ProjectionError("invalid_frontier", "frontier must be an object")
    families = DEFAULT_FAMILIES if families is None else families
    if not isinstance(families, (list, tuple)) or any(
            not isinstance(f, str) or not f or f.endswith(("_ref", "_refs")) for f in families):
        raise ProjectionError("invalid_family", "family names must omit the _ref/_refs suffix")
    families = list(dict.fromkeys(families))
    rows, fields, issues, by_pointer = [], [], [], {}
    for family in families:
        for suffix in ("_ref", "_refs"):
            key = family + suffix
            field_location = location + pointer([key])
            raw = frontier.get(key)
            field = {"field_pointer": fragment(field_location), "present": key in frontier,
                     "input_count": 0}
            fields.append(field)
            if raw is None:
                continue
            if (suffix == "_ref" and not isinstance(raw, str)) or (suffix == "_refs" and not isinstance(raw, list)):
                issues.append({"source_pointer": fragment(field_location), "code": "invalid_field_shape",
                               "message": "singular references require a string; plural references require an array"})
                continue
            refs = [raw] if suffix == "_ref" else raw
            field["input_count"] = len(refs)
            for index, ref in enumerate(refs):
                origin = fragment(field_location + (pointer([index]) if suffix == "_refs" else ""))
                try:
                    if not isinstance(ref, str) or not ref.strip():
                        raise ProjectionError("invalid_ref", "obligation reference must be a nonempty string")
                    value, resolved = resolve(document, ref)
                except ProjectionError as exc:
                    issues.append({"source_pointer": origin, "ref": ref, "code": exc.code,
                                   "message": str(exc)})
                    continue
                if resolved not in by_pointer:
                    row = {"resolved_pointer": fragment(resolved), "reference_forms": [],
                           "origins": [], "value": copy.deepcopy(value)}
                    rows.append(row)
                    by_pointer[resolved] = row
                row = by_pointer[resolved]
                if ref not in row["reference_forms"]:
                    row["reference_forms"].append(ref)
                row["origins"].append({"source_pointer": origin, "ref": ref})
    return {"status": "partial" if issues else "ok", "frontier_pointer": fragment(location),
            "fields": fields, "obligations": rows, "issues": issues,
            "interpretation": {
                "readiness": "not_evaluated", "authorization": "not_evaluated",
                "native_current_status": "not_queried",
                "completion": "Stored execution, delivery, result and acceptance fields remain distinct; no goal completion inferred",
            }}


def load_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ProjectionError("invalid_json", "duplicate object key")
            result[key] = value
        return result
    def invalid_number(_):
        raise ProjectionError("invalid_json", "non-finite JSON number")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                          parse_constant=invalid_number)
    except (UnicodeError, ValueError) as exc:
        if isinstance(exc, ProjectionError):
            raise
        raise ProjectionError("invalid_json", "document is not valid UTF-8 JSON") from exc


def encoded(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"))


def value_digest(value):
    # Sorting object keys makes unchanged detection independent of object order.
    raw = json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True,
                     separators=(",", ":")).encode("ascii")
    return hashlib.sha256(raw).hexdigest()


def value_type(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    return "number"


def read_context(document, ref, source, *, page_size=20, cursor=None,
                 if_value_sha256=None):
    """Read one snapshot without expanding descendants or acknowledging work.

    Cursor identity binds the exact source bytes, explicit file and resolved
    pointer. It is a consistency check, not a credential or a durable receipt.
    """
    if type(page_size) is not int or not 1 <= page_size <= 50:
        raise ProjectionError("invalid_page_size", "page size must be between 1 and 50")
    if if_value_sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", if_value_sha256):
        raise ProjectionError("invalid_digest", "value digest must be a lowercase SHA-256")
    if cursor is not None and if_value_sha256 is not None:
        raise ProjectionError("invalid_options", "cursor and unchanged check are separate reads")
    value, location = resolve(document, ref)
    selection = hashlib.sha256(encoded([source["path"], location]).encode("ascii")).hexdigest()
    prefix = "v1:" + source["sha256"] + ":" + selection + ":"
    offset = 0
    if cursor is not None:
        if not isinstance(cursor, str) or not cursor.startswith(prefix):
            raise ProjectionError("stale_cursor", "source or selected pointer changed; start a fresh read")
        suffix = cursor[len(prefix):]
        if not re.fullmatch(r"0|[1-9][0-9]{0,8}", suffix):
            raise ProjectionError("invalid_cursor", "invalid page offset")
        offset = int(suffix)
    result = {"status": "ok", "source": source, "resolved_pointer": fragment(location),
              "type": value_type(value), "value_sha256": value_digest(value),
              "interpretation": {"authorization": "not_evaluated", "readiness": "not_evaluated",
                                 "native_current_status": "not_queried", "receipt": "not_recorded"}}
    if if_value_sha256 == result["value_sha256"]:
        result.update(status="unchanged", content_mode="not_repeated")
    elif isinstance(value, (dict, list)):
        keys = list(value) if isinstance(value, dict) else range(len(value))
        if offset > len(keys) or (cursor is not None and offset == len(keys)):
            raise ProjectionError("invalid_cursor", "page offset is outside the selected container")
        result.update(content_mode="child_descriptors", offset=offset, total_items=len(keys),
                      items=[], next_cursor=None, descendants_included=False)
        for key in keys[offset:offset + page_size]:
            child = value[key]
            card = {"pointer": fragment(location + pointer([key])), "type": value_type(child),
                    "value_sha256": value_digest(child)}
            if isinstance(child, (dict, list)):
                card["items"] = len(child)
            elif isinstance(child, str):
                card["characters"] = len(child)
            result["items"].append(card)
            next_offset = offset + len(result["items"])
            result["next_cursor"] = prefix + str(next_offset) if next_offset < len(keys) else None
            if len(encoded(result)) + 1 > READ_OUTPUT_BYTES:
                result["items"].pop()
                break
        next_offset = offset + len(result["items"])
        result["next_cursor"] = prefix + str(next_offset) if next_offset < len(keys) else None
        if not result["items"] and len(keys) > offset:
            raise ProjectionError("entry_too_large", "child reference exceeds output bound; choose a narrower pointer locally")
    elif isinstance(value, str):
        if offset > len(value) or (cursor is not None and offset == len(value)):
            raise ProjectionError("invalid_cursor", "page offset is outside the selected string")
        end = min(offset + READ_TEXT_CHARS, len(value))
        result.update(content_mode="text", offset_characters=offset, total_characters=len(value),
                      text=value[offset:end], next_cursor=prefix + str(end) if end < len(value) else None)
    else:
        if cursor is not None:
            raise ProjectionError("invalid_cursor", "scalar value has no next page")
        result.update(content_mode="scalar", value=value, next_cursor=None)
    if len(encoded(result)) + 1 > READ_OUTPUT_BYTES:
        raise ProjectionError("output_too_large", "selected value or reference exceeds output bound")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("document", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--frontier", help="local pointer to the reference object; expands full values")
    mode.add_argument("--read", help="local pointer to read with bounded output and no descendant expansion")
    parser.add_argument("--family", action="append", help="reference prefix without _ref/_refs; repeat to select families")
    parser.add_argument("--page-size", type=int, default=20, help="--read: child descriptors per page, 1..50")
    parser.add_argument("--cursor", help="--read: continue an exact source and selection")
    parser.add_argument("--if-value-sha256", help="--read: omit content if this selected value is unchanged")
    args = parser.parse_args(argv)
    try:
        if args.read is not None:
            if args.family is not None:
                raise ProjectionError("invalid_options", "reference families apply only to frontier projection")
            with args.document.open("rb") as stream:
                raw = stream.read(READ_INPUT_BYTES + 1)
            if len(raw) > READ_INPUT_BYTES:
                raise ProjectionError("input_too_large", "document exceeds bounded read limit")
        else:
            if args.cursor is not None or args.if_value_sha256 is not None or args.page_size != 20:
                raise ProjectionError("invalid_options", "read options require --read")
            raw = args.document.read_bytes()
        source = {"path": str(args.document.absolute()),
                  "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
        document = load_json(raw)
        if args.read is not None:
            result = read_context(document, args.read, source, page_size=args.page_size,
                                  cursor=args.cursor, if_value_sha256=args.if_value_sha256)
        else:
            result = project(document, args.frontier, args.family)
            result["source"] = source
        output = encoded(result)
    except (OSError, ProjectionError, ValueError, RecursionError) as exc:
        result = {"status": "invalid", "code": getattr(exc, "code", "document_read_or_encoding"),
                  "message": str(exc) if isinstance(exc, ProjectionError) else "document could not be read or encoded"}
        print(json.dumps(result, ensure_ascii=True))
        return 1
    print(output)
    return 2 if result.get("issues") else 0


if __name__ == "__main__":
    sys.exit(main())
