"""One-shot file-metadata producer. No application import, socket or service call."""
import ast
import hashlib
import json
import os
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path

VERSION = "0.1.0"
STAGES = ("candidate", "installed", "loaded", "selection", "reserved", "effect", "health", "retirement")
MAX_OUTPUT = 16384


def field(value, selector):
    for name in selector.split(".") if selector else []:
        if not isinstance(value, dict) or name not in value:
            raise ValueError("field unavailable")
        value = value[name]
    return value


class Reader:
    def __init__(self, specs):
        if not isinstance(specs, dict) or len(specs) > 32:
            raise ValueError("source selection limit")
        self.specs, self.cache, self.refs = specs, {}, {}

    def read(self, name):
        if name in self.cache:
            return self.cache[name]
        spec = self.specs[name]
        path = Path(spec["path"])
        allowed = Path(spec["allowed_root"]).resolve()
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError("invalid source path")
        resolved = path.resolve(strict=True)
        resolved.relative_to(allowed)
        before = path.lstat() if spec["kind"] == "link" else resolved.stat()
        if spec["kind"] == "link":
            if not stat.S_ISLNK(before.st_mode):
                raise ValueError("expected explicit pointer")
            raw = os.readlink(path).encode()
            value = str(resolved)
            after = path.lstat()
        elif spec["kind"] == "stat":
            if not stat.S_ISREG(before.st_mode):
                raise ValueError("not ordinary executable")
            raw = None
            value = str(resolved)
            after = resolved.stat()
        else:
            if not stat.S_ISREG(before.st_mode) or before.st_size > 65536:
                raise ValueError("source size/type")
            with resolved.open("rb") as stream:
                raw = stream.read(65537)
            after = resolved.stat()
            if len(raw) > 65536:
                raise ValueError("source size")
            if spec["kind"] == "json":
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise ValueError("source object")
            elif spec["kind"] == "constant":
                tree = ast.parse(raw)
                values = [ast.literal_eval(n.value) for n in tree.body
                          if isinstance(n, ast.Assign) and len(n.targets) == 1
                          and isinstance(n.targets[0], ast.Name) and n.targets[0].id == spec["constant"]]
                if len(values) != 1:
                    raise ValueError("constant identity")
                value = values[0]
            elif spec["kind"] == "file":
                value = hashlib.sha256(raw).hexdigest()
            else:
                raise ValueError("unsupported source kind")
        identity = lambda x: (x.st_dev, x.st_ino, x.st_size, x.st_mtime_ns)
        if identity(before) != identity(after) or resolved != path.resolve(strict=True):
            raise ValueError("source changed during observation")
        self.refs[name] = {"id": name, "path": str(path),
                           "sha256": hashlib.sha256(raw).hexdigest() if raw is not None else None,
                           "mtime_ns": after.st_mtime_ns}
        self.cache[name] = value
        return value


def stage_value(spec, reader):
    refs = []
    try:
        for name in spec.get("refs", []):
            reader.read(name)
            refs.append(name)
        op = spec.get("op", "literal")
        if op == "literal":
            value = spec.get("value")
        elif op == "field":
            value = field(reader.read(spec["source"]), spec.get("field", ""))
            refs.append(spec["source"])
            if "expected" in spec and value != spec["expected"]:
                raise ValueError("field identity mismatch")
        elif op == "matches":
            for name, expected in spec["digests"].items():
                if reader.read(name) != expected:
                    raise ValueError("source digest mismatch")
                refs.append(name)
            value = spec["value"]
        elif op == "bindings":
            for left, lp, right, rp in spec["checks"]:
                if field(reader.read(left), lp) != field(reader.read(right), rp):
                    raise ValueError("related source mismatch")
                refs.extend([left, right])
            value = spec["value"]
        elif op == "normal_entry":
            value = reader.read(spec["source"])
            refs.append(spec["source"])
            result = value["tool_result"]
            if (result["exit_code"] not in (0, 2)
                    or json.loads(result["output"])["schema"] != "workstation-entry-result-v1"):
                raise ValueError("normal entry not observed")
            value = "READ_ONLY_ENTRY_ONLY"
        else:
            raise ValueError("unsupported stage operation")
        source_time = spec.get("source_time")
        if "source_time_field" in spec:
            name, selector = spec["source_time_field"]
            source_time = field(reader.read(name), selector)
            refs.append(name)
        return {"status": spec["status"], "value": value,
                "source_time": source_time, "refs": list(dict.fromkeys(refs))}
    except (OSError, ValueError, TypeError, KeyError, SyntaxError, UnicodeError):
        return {"status": "UNKNOWN", "value": "SOURCE_UNREADABLE_OR_UNBOUND",
                "source_time": None, "refs": list(dict.fromkeys(refs)), "error": "source_observation_failed"}


def collect(profile):
    if profile.get("schema") != "node-asset-selection-v1" or not 1 <= len(profile.get("assets", [])) <= 8:
        raise ValueError("invalid node selection")
    reader = Reader(profile["sources"])
    rows = []
    for asset in profile["assets"]:
        if set(asset["stages"]) != set(STAGES):
            raise ValueError("eight separate dimensions required")
        stages = {key: stage_value(asset["stages"][key], reader) for key in STAGES}
        # This producer does not test services, health or capacity.
        if stages["health"]["status"] not in ("UNKNOWN", "NOT_ASSESSED"):
            raise ValueError("health assertion outside producer capability")
        rows.append({"id": asset["id"], "version_label": asset["version_label"],
                     "owner_ref": asset.get("owner_ref"), "diagnostic_refs": asset.get("diagnostic_refs", []),
                     "stages": stages})
        if any(stages[key].get("error") for key in ("installed", "selection")):
            rows[-1]["version_label"] = "UNVERIFIED"
    return {"schema": "node-asset-facts-v1", **profile["identity"],
            "producer_version": VERSION, "observation_method": "EXACT_ORDINARY_FILE_METADATA_ONLY",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "status": "RUNTIME_UNVERIFIED", "runtime_health_assessed": False,
            "assets": rows, "source_refs": list(reader.refs.values())}


def publish(path, value):
    """Atomic output in the explicitly selected existing candidate directory."""
    path = Path(path)
    raw = (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
    if len(raw) > MAX_OUTPUT:
        raise ValueError("observation exceeds normal reader limit")
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError("output must be an ordinary selected path")
    fd, temporary = tempfile.mkstemp(prefix=".asset-observation-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        profile = json.loads(args.selection.read_text())
        result = collect(profile)
        publish(args.output, result)
    except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
        print(json.dumps({"status": "OBSERVATION_FAILED", "category": type(exc).__name__,
                          "published": False, "runtime_health_assessed": False}))
        return 2
    print(json.dumps({"status": "METADATA_PUBLISHED_RUNTIME_UNVERIFIED", "published": True,
                      "asset_count": len(result["assets"]), "runtime_health_assessed": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
