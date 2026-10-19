"""Read selected local references without deciding authorization or task readiness.

Only the explicit document is opened. Referenced values are returned intact; no
other files, native tasks, messages, scheduling or persistent state are touched.
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("document", type=Path)
    parser.add_argument("--frontier", required=True, help="local pointer to the reference object")
    parser.add_argument("--family", action="append", help="reference prefix without _ref/_refs; repeat to select families")
    args = parser.parse_args(argv)
    try:
        raw = args.document.read_bytes()
        result = project(load_json(raw), args.frontier, args.family)
        result["source"] = {"path": str(args.document.absolute()),
                            "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
        output = json.dumps(result, ensure_ascii=True, allow_nan=False)
    except (OSError, ProjectionError, ValueError) as exc:
        result = {"status": "invalid", "code": getattr(exc, "code", "document_read_or_encoding"),
                  "message": str(exc)}
        print(json.dumps(result, ensure_ascii=True))
        return 1
    print(output)
    return 2 if result["issues"] else 0


if __name__ == "__main__":
    sys.exit(main())
