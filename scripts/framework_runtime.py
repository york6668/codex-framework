"""Version-bound execution and recoverable framework-only installation.

Code comes only from explicit Git objects in the approved source repository.
Document snapshots remain data and never grant code installation authority.
"""

import ast
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile

import codex_framework as cf

FORMAT = 1
INPUT_CONTRACT = 1
MODULES = ("codex_framework.py", "execution_check.py", "framework_refresh.py",
           "source_evidence.py", "workstation_check.py", "workstation_entry.py",
           "framework_runtime.py", "coordination_files.py")
# Historical package identities remain readable for approved rollback. They do
# not add retired code to a new package or migrate any node-owned task records.
LEGACY_RUNTIME_MODULES = ("codex_framework.py", "execution_check.py", "framework_refresh.py",
           "source_evidence.py", "workstation_check.py", "workstation_entry.py",
           "framework_runtime.py", "task_runtime.py", "task_worker.py", "task_coordination.py",
           "task_results.py", "delivery_relations.py", "coordination_files.py")
LEGACY_MODULES = LEGACY_RUNTIME_MODULES[:7]
SUPPORTED_MODULES = (LEGACY_MODULES, LEGACY_RUNTIME_MODULES[:9], LEGACY_RUNTIME_MODULES[:10],
                     LEGACY_RUNTIME_MODULES[:12], LEGACY_RUNTIME_MODULES, MODULES)
SHA = re.compile(r"[0-9a-f]{40}\Z")


def declared_modules(raw):
    """Parse the approved source's closure without executing candidate code."""
    try:
        tree = ast.parse(raw)
    except (SyntaxError, TypeError, ValueError) as exc:
        raise cf.SyncError("候选模块声明无法解析") from exc
    declarations = [node for node in tree.body if isinstance(node, ast.Assign)
                    and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id == "MODULES"]
    try:
        value = ast.literal_eval(declarations[0].value) if len(declarations) == 1 else None
    except (ValueError, TypeError):
        value = None
    if value not in SUPPORTED_MODULES:
        raise cf.SyncError("候选模块闭包不兼容")
    return value


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def root(f):
    return cf.safe_path(f.codex_home, "framework-sync/runtime")


def pointer(f):
    return cf.safe_path(root(f), "active.json")


def journal(f):
    return cf.safe_path(root(f), "transaction.json")


def load(path):
    raw = cf.read_file(path)
    try:
        if raw is None:
            return None
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise cf.SyncError("运行状态必须是对象: " + path.name)
        return value
    except (TypeError, ValueError) as exc:
        raise cf.SyncError("运行状态 JSON 无效: " + path.name) from exc


def matches_sha(value, length=40):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{" + str(length) + "}", value) is not None


def pointer_value(value):
    if (not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != FORMAT
            or not matches_sha(value.get("revision")) or not matches_sha(value.get("manifest_sha256"), 64)
            or not isinstance(value.get("source_repo"), str) or not Path(value["source_repo"]).is_absolute()
            or (value.get("previous_revision") is not None and not matches_sha(value["previous_revision"]))):
        raise cf.SyncError("运行指针对象或字段类型无效")
    return value


def active(f):
    value = load(pointer(f))
    return None if value is None else pointer_value(value)


def sync_state(f):
    state = f.read_state().get("sync", {})
    for field in ("observed_revision", "applied_revision"):
        if state.get(field) is not None and not matches_sha(state[field]):
            raise cf.SyncError("正文同步版本字段类型无效: " + field)
    for field in ("local_overrides", "conflicts"):
        value = state.get(field, [])
        if not isinstance(value, list) or any(not isinstance(n, str) or not cf.allowed(n) for n in value):
            raise cf.SyncError("正文同步路径数组无效: " + field)
    if "status" in state and not isinstance(state["status"], str):
        raise cf.SyncError("正文同步状态字段无效")
    return state


def validate_release_version(value):
    """Product versions are names; FORMAT and Git hashes serve other contracts."""
    if not isinstance(value, str) or re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", value) is None:
        raise cf.SyncError("核心产品版本号须为无前导零的 major.minor.patch")
    return value


def declared_release_version(source):
    """Read the single literal product declaration without executing its code."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, TypeError) as exc:
        raise cf.SyncError("核心产品版本声明无法解析") from exc
    values = [n.value for n in tree.body if isinstance(n, ast.Assign)
              and any(isinstance(t, ast.Name) and t.id == "RELEASE_VERSION" for t in n.targets)]
    # The installed macOS runner can be Python 3.9, before match AST nodes.
    match_names = tuple(getattr(ast, name) for name in ("MatchAs", "MatchStar") if hasattr(ast, name))
    match_mappings = (ast.MatchMapping,) if hasattr(ast, "MatchMapping") else ()
    # Include explicit deletion and Python's non-Name binding syntax. A
    # declaration removed/rebound later is not a persistent product version.
    writes = [n for n in ast.walk(tree) if
              (isinstance(n, ast.Name) and n.id == "RELEASE_VERSION"
               and isinstance(n.ctx, (ast.Store, ast.Del)))
              or (isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                  and n.name == "RELEASE_VERSION")
              or (isinstance(n, ast.alias) and (n.asname or n.name.split(".")[0]) == "RELEASE_VERSION")
              or (isinstance(n, ast.ExceptHandler) and n.name == "RELEASE_VERSION")
              or (isinstance(n, match_names) and n.name == "RELEASE_VERSION")
              or (isinstance(n, match_mappings) and n.rest == "RELEASE_VERSION")]
    if not values and not writes:
        return None  # Existing unnumbered releases are not relabelled.
    if len(values) != 1 or len(writes) != 1 or not isinstance(values[0], ast.Constant):
        raise cf.SyncError("核心产品版本须为唯一字符串常量")
    return validate_release_version(values[0].value)


def version_path(f, revision):
    if not isinstance(revision, str) or not SHA.fullmatch(revision):
        raise cf.SyncError("运行包 revision 须为完整 Git commit SHA；产品版本另行选择")
    return cf.safe_path(root(f), "versions/" + revision)


def validate(directory, expected_hash=None):
    manifest_path = cf.safe_path(directory, "manifest.json")
    raw = cf.read_file(manifest_path)
    if raw is None or (expected_hash and digest(raw) != expected_hash):
        raise cf.SyncError("运行包 manifest 缺失或 hash 不符")
    try:
        m = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise cf.SyncError("运行包 manifest JSON 无效") from exc
    if not isinstance(m, dict):
        raise cf.SyncError("运行包 manifest 必须是对象")
    required = set(m.get("files", {})) if isinstance(m.get("files"), dict) else set()
    closures = [{"scripts/" + n for n in names} | {"snapshot.json"} for names in SUPPORTED_MODULES]
    if (type(m.get("version")) is not int or type(m.get("input_contract")) is not int
            or m.get("version") != FORMAT or m.get("input_contract") != INPUT_CONTRACT
            or m.get("source") != cf.REMOTE or m.get("branch") != cf.BRANCH
            or not matches_sha(m.get("revision")) or not isinstance(m.get("files"), dict)
            or required not in closures):
        raise cf.SyncError("运行包来源、完整模块闭包或输入契约不兼容")
    actual = set()
    for path in directory.rglob("*"):
        if path.is_symlink():
            raise cf.SyncError("运行包包含符号链接")
        if path.is_file():
            actual.add(path.relative_to(directory).as_posix())
    if actual != required | {"manifest.json"}:
        raise cf.SyncError("运行包包含未列文件或缺件")
    for name, item in m["files"].items():
        expected_mode = "100755" if name == "scripts/codex_framework.py" else "100644"
        if (not isinstance(item, dict) or set(item) != {"sha256", "mode"}
                or not matches_sha(item.get("sha256"), 64) or item.get("mode") != expected_mode):
            raise cf.SyncError("运行包文件身份字段无效: " + name)
        path = cf.safe_path(directory, name)
        value = cf.read_file(path)
        mode = "100755" if path.stat().st_mode & 0o111 else "100644"
        if value is None or digest(value) != item["sha256"] or mode != item["mode"]:
            raise cf.SyncError("运行包文件 hash/模式不符: " + name)
    declaration = declared_modules(cf.read_file(cf.safe_path(directory, "scripts/framework_runtime.py")))
    if required != {"scripts/" + n for n in declaration} | {"snapshot.json"}:
        raise cf.SyncError("运行包模块与源码声明不符")
    import framework_refresh as refresh
    snapshot = load(cf.safe_path(directory, "snapshot.json"))
    refresh.validate_snapshot(snapshot)
    if snapshot["revision"] != m["revision"]:
        raise cf.SyncError("运行包正文与工具版本不符")
    return m, snapshot


def executing():
    context = getattr(sys, "_codex_framework_runtime", None)
    if context is not None:
        if (not isinstance(context, dict) or not matches_sha(context.get("revision"))
                or not isinstance(context.get("directory"), str) or not Path(context["directory"]).is_absolute()
                or (context.get("source_repo") is not None and
                    (not isinstance(context["source_repo"], str) or not Path(context["source_repo"]).is_absolute()))):
            raise cf.SyncError("执行上下文身份字段无效")
        return context
    directory = Path(__file__).resolve().parent.parent
    if (directory / "manifest.json").is_file():
        manifest = load(directory / "manifest.json")
        if not matches_sha(manifest.get("revision")):
            raise cf.SyncError("执行包版本字段无效")
        return {"directory": str(directory), "revision": manifest["revision"]}
    return {}


def default_repo():
    context = executing()
    return Path(context.get("source_repo") or Path(__file__).resolve().parent.parent)


def describe(f, state=None):
    if journal(f).exists():
        transaction_value(load(journal(f)))
    a = active(f)
    state = sync_state(f) if state is None else state
    context = executing()
    overrides = {name for name, value in f.load_state().items()
                 if cf.read_file(f.target(name)) != value}
    from execution_check import loaded_tool_version
    result = {"active_revision": a["revision"] if a else None,
              "tool_revision": context.get("revision"), "tool_directory": context.get("directory"),
              "tool_version": loaded_tool_version(), "active_version": None, "document_version": None,
              "document_revision": state.get("applied_revision"), "input_contract": INPUT_CONTRACT,
              "local_overrides": sorted(overrides), "status": "unmanaged"}
    if not a:
        if journal(f).exists():
            result["status"] = "update-incomplete"
        return result
    try:
        m, snapshot = validate(version_path(f, a["revision"]), a["manifest_sha256"])
        result["package_manifest_sha256"] = a["manifest_sha256"]
        result["package_input_contract"] = m["input_contract"]
        result["active_version"] = declared_release_version(
            cf.read_file(version_path(f, a["revision"]) / "scripts/codex_framework.py"))
    except (cf.SyncError, OSError, ValueError, KeyError) as exc:
        result.update(status="invalid-package", reason=str(exc))
        return result
    # A document-only refresh legitimately moves beyond the active code package.
    # Its current deployment baseline identifies local edits; the older package
    # is only a document authority while both revisions still match.
    if result["document_revision"] == a["revision"]:
        for name, value in snapshot["files"].items():
            if cf.read_file(f.target(name)) != value.encode():
                overrides.add(name)
    result["local_overrides"] = sorted(overrides)
    if result["document_revision"] == a["revision"] and not overrides:
        result["document_version"] = result["active_version"]
    result["status"] = ("update-incomplete" if journal(f).exists() else
                        "revision-mismatch" if result["document_revision"] != a["revision"] else
                        "source-invocation" if not result["tool_revision"] else
                        "running-previous" if result["tool_revision"] != a["revision"] else
                        "local-overrides" if overrides else "matched")
    return result


# The stable loader contains no repository imports. Each launch resolves one
# physical directory before loading any runtime module; retained versions are
# never removed by an update, so an already running P can finish after Q switches.
LOADER = '''#!/usr/bin/env python3
# codex-framework managed loader v1
import ast, base64, fcntl, hashlib, json, os, re, runpy, sys
from pathlib import Path
ROOT = Path(%r)
MODULES = %r
# Frozen identities for packages installed before the legacy task entry retired.
LEGACY_MODULES = ("codex_framework.py", "execution_check.py", "framework_refresh.py",
                 "source_evidence.py", "workstation_check.py", "workstation_entry.py",
                 "framework_runtime.py", "task_runtime.py", "task_worker.py", "task_coordination.py",
                 "task_results.py", "delivery_relations.py", "coordination_files.py")
SUPPORTED_MODULES = (LEGACY_MODULES[:7], LEGACY_MODULES[:9], LEGACY_MODULES[:10],
                     LEGACY_MODULES[:12], LEGACY_MODULES, MODULES)

def read(path):
    for item in (path, *path.parents):
        if item.is_symlink():
            raise ValueError("symlink in runtime path")
    return path.read_bytes()

def object_value(value, label):
    if not isinstance(value, dict):
        raise ValueError(label + " must be an object")
    return value

def sha(value, length):
    return isinstance(value, str) and re.fullmatch("[0-9a-f]{" + str(length) + "}", value) is not None

def pointer_value(value):
    a = object_value(value, "runtime pointer")
    if (type(a.get("version")) is not int or a["version"] != 1
            or not sha(a.get("revision"), 40) or not sha(a.get("manifest_sha256"), 64)
            or not isinstance(a.get("source_repo"), str) or not Path(a["source_repo"]).is_absolute()
            or (a.get("previous_revision") is not None and not sha(a["previous_revision"], 40))):
        raise ValueError("invalid runtime pointer fields")
    return a

def manifest_value(value, revision):
    m = object_value(value, "runtime manifest")
    names = set(m.get("files", {})) if isinstance(m.get("files"), dict) else set()
    closures = [{"scripts/" + name for name in group} | {"snapshot.json"} for group in SUPPORTED_MODULES]
    if (type(m.get("version")) is not int or m["version"] != 1
            or type(m.get("input_contract")) is not int or m["input_contract"] != 1
            or m.get("revision") != revision or not isinstance(m.get("files"), dict)
            or names not in closures):
        raise ValueError("runtime modules/contract mismatch")
    for name, entry in m["files"].items():
        expected_mode = "100755" if name == "scripts/codex_framework.py" else "100644"
        if (not isinstance(entry, dict) or set(entry) != {"sha256", "mode"}
                or not sha(entry.get("sha256"), 64) or entry.get("mode") != expected_mode):
            raise ValueError("invalid runtime file identity fields: " + name)
    return m, names

def recovery_candidate(value):
    tx = object_value(value, "runtime transaction")
    if type(tx.get("version")) is not int or tx["version"] != 1 or not isinstance(tx.get("changes"), dict):
        raise ValueError("invalid runtime transaction fields")
    if "previous" not in tx:
        raise ValueError("missing previous runtime identity")
    if tx["previous"] is not None:
        pointer_value(tx["previous"])
    for key, item in tx["changes"].items():
        if not isinstance(item, dict) or set(item) != {"before", "after"}:
            raise ValueError("invalid runtime transaction file record")
        for value in item.values():
            if not isinstance(value, dict) or set(value) not in ({"data"}, {"link"}):
                raise ValueError("invalid runtime transaction file value")
            if "link" in value:
                if key != "launcher" or not isinstance(value["link"], str):
                    raise ValueError("invalid runtime transaction link")
            elif value["data"] is not None:
                if not isinstance(value["data"], str):
                    raise ValueError("invalid runtime transaction content")
                base64.b64decode(value["data"], validate=True)
    return pointer_value(tx.get("candidate"))

def main():
    lock = ROOT / "framework-sync/lock"
    for path in (lock, *lock.parents):
        if path.is_symlink():
            raise ValueError("symlink in runtime lock path")
    with lock.open("rb") as stream:
        fcntl.flock(stream, fcntl.LOCK_SH | fcntl.LOCK_NB)
        runtime = ROOT / "framework-sync/runtime"
        p = runtime / "active.json"
        pending = runtime / "transaction.json"
        candidate = recovery_candidate(json.loads(read(pending))) if pending.exists() else None
        if p.exists():
            a = pointer_value(json.loads(read(p)))
        elif candidate is not None and any(x in sys.argv[1:] for x in ("runtime-recover", "sync-status")):
            a = candidate
        else:
            raise ValueError("no active runtime; use the prepared version's runtime-recover")
        revision = a["revision"]
        directory = runtime / "versions" / revision
        raw = read(directory / "manifest.json")
        if hashlib.sha256(raw).hexdigest() != a["manifest_sha256"]:
            raise ValueError("runtime manifest hash mismatch")
        m, names = manifest_value(json.loads(raw), revision)
        actual = {p.relative_to(directory).as_posix() for p in directory.rglob("*") if p.is_file() or p.is_symlink()}
        if actual != names | {"manifest.json"}:
            raise ValueError("unexpected runtime payload")
        for name, entry in m["files"].items():
            path = directory / name
            data = read(path)
            mode = "100755" if path.stat().st_mode & 0o111 else "100644"
            if hashlib.sha256(data).hexdigest() != entry["sha256"] or mode != entry["mode"]:
                raise ValueError("runtime hash/mode mismatch: " + name)
        try:
            tree = ast.parse(read(directory / "scripts/framework_runtime.py"))
            declarations = [n for n in tree.body if isinstance(n, ast.Assign) and len(n.targets)==1 and isinstance(n.targets[0], ast.Name) and n.targets[0].id=="MODULES"]
            modules = ast.literal_eval(declarations[0].value) if len(declarations)==1 else None
        except (SyntaxError, ValueError, TypeError) as exc:
            raise ValueError("invalid runtime module declaration") from exc
        if modules not in SUPPORTED_MODULES or {"scripts/" + n for n in modules} | {"snapshot.json"} != names:
            raise ValueError("runtime manifest/source closure mismatch")
        sys.path.insert(0, str(directory / "scripts"))
        sys.dont_write_bytecode = True
        sys._codex_framework_runtime = {"directory": str(directory), "revision": revision, "source_repo": a["source_repo"]}
    runpy.run_path(str(directory / "scripts/codex_framework.py"), run_name="__main__")

def unavailable(exc):
    identity = {"status":"runtime-unavailable", "reason":str(exc), "tool_revision":None,
                "active_revision":None, "document_revision":None, "input_contract_supported":1}
    gaps = []
    try:
        value = object_value(json.loads(read(ROOT / "framework-sync/runtime/active.json")), "runtime pointer")
        if not sha(value.get("revision"), 40):
            raise ValueError("invalid active revision")
        identity["active_revision"] = value["revision"]
    except (OSError, ValueError, KeyError):
        gaps.append("active_identity")
    try:
        state = object_value(json.loads(read(ROOT / "framework-sync/state.json")), "document state")
        if type(state.get("version")) is not int or state["version"] != 1:
            raise ValueError("invalid document state version")
        sync = object_value(state.get("sync"), "document sync")
        revision = sync.get("applied_revision")
        if revision is not None and not sha(revision, 40):
            raise ValueError("invalid document revision")
        identity["document_revision"] = revision
    except (OSError, ValueError, KeyError):
        gaps.append("document_identity")
    if gaps:
        identity["identity_gaps"] = gaps
    return identity

try:
    main()
except (OSError, ValueError, KeyError) as exc:
    if "session-start" in sys.argv[1:]:
        context = ("Codex framework runtime unknown: " + str(exc)).encode()[:1700].decode("utf-8", "ignore")
        print(json.dumps({"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":context}}))
        sys.exit(0)
    print(json.dumps(unavailable(exc)))
    sys.exit(1)
'''


def loader_bytes(f):
    return (LOADER % (str(f.codex_home), MODULES)).encode()



def prior_loader_bytes(f, prior):
    """Read an approved package's exact loader without executing its source."""
    if prior is None:
        return None
    fingerprint = approved_packages(f).get(prior["revision"])
    if fingerprint != prior["manifest_sha256"]:
        raise cf.SyncError("旧运行入口没有匹配的已批准运行包")
    directory = version_path(f, prior["revision"])
    manifest, _ = validate(directory, fingerprint)
    if manifest["revision"] != prior["revision"]:
        raise cf.SyncError("旧运行入口的包与活动版本不符")
    raw = cf.read_file(cf.safe_path(directory, "scripts/framework_runtime.py"))
    modules = declared_modules(raw)
    tree = ast.parse(raw)
    declarations = [node for node in tree.body if isinstance(node, ast.Assign)
                    and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id == "LOADER"]
    try:
        template = ast.literal_eval(declarations[0].value) if len(declarations) == 1 else None
        if not isinstance(template, str):
            raise ValueError("missing literal loader")
        return (template % (str(f.codex_home), modules)).encode()
    except (TypeError, ValueError, UnicodeError) as exc:
        raise cf.SyncError("旧运行入口模板无法静态核对") from exc


def managed_loader_matches(f, raw, prior):
    if raw == loader_bytes(f):
        return True
    if prior is None:
        return False
    if raw == prior_loader_bytes(f, prior):
        return True
    # A previous installer can switch the active package while leaving its own
    # launcher. Accept only that exact, still-approved predecessor template.
    revision = prior.get("previous_revision")
    fingerprint = approved_packages(f).get(revision)
    return fingerprint is not None and raw == prior_loader_bytes(
        f, {"revision": revision, "manifest_sha256": fingerprint})


def launcher_path(f):
    cf.safe_path(f.home, ".local/bin")
    return f.home / ".local/bin/codex-framework"


def stable_command(f):
    a = active(f)
    if not a or not managed_loader_matches(f, cf.read_file(launcher_path(f)), a):
        raise cf.SyncError("先安装受管理运行包，再配置稳定 Hook 入口")
    validate(version_path(f, a["revision"]), a["manifest_sha256"])
    return shlex.join([sys.executable, str(launcher_path(f)), "--home", str(f.home),
                       "--codex-home", str(f.codex_home), "session-start"])


def capture(path):
    if path.is_symlink():
        return {"link": os.readlink(path)}
    value = cf.read_file(path)
    return {"data": base64.b64encode(value).decode() if value is not None else None}


def file_value(value):
    return {"data": base64.b64encode(value).decode() if value is not None else None}


def target(f, key):
    if cf.allowed(key):
        return f.target(key)
    paths = {"state": f.state_path, "cache": cf.safe_path(f.codex_home, "framework-sync/upstream.json"),
             "active": pointer(f), "hook": cf.safe_path(f.codex_home, "hooks.json"),
             "launcher": launcher_path(f)}
    if key not in paths:
        raise cf.SyncError("恢复记录包含范围外目标")
    return paths[key]


def replace(path, value, *, launcher=False):
    if "link" in value:
        if not launcher:
            raise cf.SyncError("只有已核对的旧 launcher 可恢复符号链接")
        temporary = path.with_name(path.name + ".restore-" + str(os.getpid()))
        try:
            temporary.symlink_to(value["link"])
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    elif launcher and path.is_symlink():
        data = base64.b64decode(value["data"], validate=True)
        fd, name = tempfile.mkstemp(dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
            os.chmod(name, 0o755)
            os.replace(name, path)
        finally:
            Path(name).unlink(missing_ok=True)
    else:
        cf.write_file(path, base64.b64decode(value["data"], validate=True) if value["data"] is not None else None)
        if launcher and value["data"] is not None:
            path.chmod(0o755)


def approved_snapshot(f, revision):
    import framework_refresh as refresh
    if cf.git(f.repo, "config", "--get", "remote.origin.url") not in (
            cf.REMOTE, cf.REMOTE.removesuffix(".git"), "git@github.com:york6668/codex-framework.git"):
        raise cf.SyncError("运行代码须来自明确获准的框架 Git 来源")
    if not SHA.fullmatch(revision):
        raise cf.SyncError("运行安装要求完整 commit SHA")
    candidates = [cf.git(f.repo, "rev-parse", ref, optional=True) for ref in
                  ("refs/heads/" + cf.BRANCH, "refs/remotes/origin/" + cf.BRANCH)]
    if revision not in candidates:
        raise cf.SyncError("待安装提交不是已核对的发布分支头；先取得并核对确切来源")
    return refresh.snapshot_at_revision(f, revision)


def approved_packages(f):
    catalog = load(cf.safe_path(root(f), "approved.json")) or {}
    if not isinstance(catalog, dict) or any(not matches_sha(k) or not matches_sha(v, 64)
                                            for k, v in catalog.items()):
        raise cf.SyncError("运行包历史完整性记录无效")
    return catalog


def register(f, revision, manifest):
    catalog = approved_packages(f)
    fingerprint = digest(encoded(manifest))
    if revision in catalog and catalog[revision] != fingerprint:
        raise cf.SyncError("同一运行版本身份变化；不覆盖已有成果")
    catalog[revision] = fingerprint
    cf.write_file(cf.safe_path(root(f), "approved.json"), encoded(catalog))


def prepare(f, revision):
    snapshot = approved_snapshot(f, revision)
    values = {"snapshot.json": (encoded(snapshot), "100644")}
    modules = declared_modules(cf.git(f.repo, "show", revision + ":scripts/framework_runtime.py"))
    for name in modules:
        path = "scripts/" + name
        entry = cf.git(f.repo, "ls-tree", revision, "--", path)
        expected_mode = "100755" if name == "codex_framework.py" else "100644"
        if not entry or entry.split()[0:2] != [expected_mode, "blob"]:
            raise cf.SyncError("运行模块缺失、链接或模式无效: " + path)
        blob = entry.split()[2]
        raw = subprocess.run(["git", "-C", str(f.repo), "cat-file", "blob", blob],
                             capture_output=True, check=True, timeout=10).stdout
        try:
            compile(raw, path, "exec")  # Parse only; no candidate code runs before validation.
        except (SyntaxError, ValueError) as exc:
            raise cf.SyncError("运行模块无法编译: " + path) from exc
        values[path] = (raw, expected_mode)
    tree = ast.parse(values["scripts/framework_runtime.py"][0])
    constants = {node.targets[0].id: node.value.value for node in tree.body
                 if isinstance(node, ast.Assign) and len(node.targets) == 1
                 and isinstance(node.targets[0], ast.Name) and isinstance(node.value, ast.Constant)}
    if (type(constants.get("FORMAT")) is not int or type(constants.get("INPUT_CONTRACT")) is not int
            or constants.get("FORMAT") != FORMAT or constants.get("INPUT_CONTRACT") != INPUT_CONTRACT):
        raise cf.SyncError("候选运行包/输入契约不兼容；活动版本保持")
    manifest = {"version": FORMAT, "source": cf.REMOTE, "branch": cf.BRANCH,
                "revision": revision, "input_contract": INPUT_CONTRACT,
                "files": {n: {"sha256": digest(b), "mode": mode} for n, (b, mode) in values.items()}}
    declared_release_version(values["scripts/codex_framework.py"][0])
    directory = version_path(f, revision)
    if directory.exists():
        validate(directory, digest(encoded(manifest)))
        register(f, revision, manifest)
        return directory, manifest, snapshot
    directory.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".prepare-", dir=directory.parent))
    try:
        for name, (value, mode) in values.items():
            path = cf.safe_path(staging, name)
            cf.write_file(path, value)
            path.chmod(0o755 if mode == "100755" else 0o644)
        cf.write_file(staging / "manifest.json", encoded(manifest))
        (staging / "manifest.json").chmod(0o644)
        validate(staging, digest(encoded(manifest)))
        with tempfile.TemporaryDirectory(prefix="runtime-smoke-") as td:
            home = Path(td).resolve()
            for command, payload, exits in (("sync-status", None, (0,)), ("check", "{}", (1,)),
                                            ("workstation-check", "{}", (1,)), ("workstation-entry", None, (2,))):
                try:
                    run = subprocess.run([sys.executable, str(staging / "scripts/codex_framework.py"),
                                          "--home", str(home), command], input=payload, text=True,
                                         capture_output=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"), timeout=5)
                    try:
                        valid_json = isinstance(json.loads(run.stdout), dict)
                    except (ValueError, TypeError):
                        valid_json = False
                    failure = (run.returncode not in exits or "Traceback" in run.stderr or not valid_json)
                    diagnostic = {"revision": revision, "command": command, "exit_code": run.returncode,
                                  "stdout": run.stdout[:4096], "stderr": run.stderr[:4096]}
                except (OSError, subprocess.SubprocessError) as exc:
                    failure = True
                    diagnostic = {"revision": revision, "command": command, "error": type(exc).__name__}
                if failure:
                    cf.write_file(cf.safe_path(root(f), "failures/" + revision + "-" + command + ".json"), encoded(diagnostic))
                    raise cf.SyncError("候选运行入口烟测失败: " + command + "；原版本保持，诊断留在运行目录 failures")
        os.rename(staging, directory)
    except BaseException:
        # Keep failed prepared bytes for diagnosis; they are never active.
        raise
    register(f, revision, manifest)
    return directory, manifest, snapshot


def hook_groups(config):
    """Validate editable containers; opaque handlers/groups remain untouched."""
    if not isinstance(config, dict):
        raise cf.SyncError("hooks.json 必须是对象")
    hooks = config.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise cf.SyncError("hooks 必须是对象")
    groups = hooks.setdefault("SessionStart", [])
    if not isinstance(groups, list):
        raise cf.SyncError("SessionStart 必须是数组")
    return groups


def hook_identity(f, handler):
    """Match a known entry AND provable effective CLI targets, without executing it.

    Generated handlers carry explicit --home. An omitted home, shell wrapper,
    unknown option or entry has no proven ownership and is preserved.
    """
    if not isinstance(handler, dict) or handler.get("type") != "command":
        return None
    command = handler.get("command")
    if not isinstance(command, str) or not command or any(v in command for v in ("$", "`", "\n")):
        return None
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>()")
        lexer.whitespace_split = True
        lexer.commenters = ""
        args = list(lexer)
    except ValueError:
        return None
    if not args:
        return None
    source = str(f.repo / "scripts/codex_framework.py")
    stable = str(launcher_path(f))
    index = 0
    if args[0] not in (source, stable):
        if (not Path(args[0]).is_absolute()
                or not re.fullmatch(r"python(?:3(?:\.\d+)?)?", Path(args[0]).name)):
            return None
        index = 1
        while index < len(args) and args[index] in ("-B", "-u"):
            index += 1
        if index == len(args) or args[index] not in (source, stable):
            return None
    options, commands = {}, 0
    cursor = index + 1
    while cursor < len(args):
        token = args[cursor]
        if token == "session-start":
            commands += 1
            cursor += 1
            continue
        flag, separator, value = token.partition("=")
        if flag not in ("--home", "--codex-home", "--repo"):
            return None
        if not separator:
            cursor += 1
            if cursor == len(args):
                return None
            value = args[cursor]
        if not value or not Path(value).is_absolute() or ".." in Path(value).parts:
            return None
        options[flag] = os.path.normpath(value)  # argparse uses the final occurrence.
        cursor += 1
    home = options.get("--home")
    if commands != 1 or home is None:
        return None
    codex_home = options.get("--codex-home", str(Path(home) / ".codex"))
    if (home != os.path.normpath(str(f.home)) or codex_home != os.path.normpath(str(f.codex_home))
            or options.get("--repo", os.path.normpath(str(f.repo))) != os.path.normpath(str(f.repo))):
        return None
    return {"argv":args, "entry_index":index, "target":(home, codex_home),
            "runner":args[:index], "repo":options.get("--repo", os.path.normpath(str(f.repo)))}


def migrated_hook_command(f, handler, identity):
    args = list(identity["argv"])
    args[identity["entry_index"]] = str(launcher_path(f))
    # Keep the original interpreter/options/target arguments; do not reassign ownership.
    return shlex.join(args)


def hook_update(f):
    old = cf.read_file(target(f, "hook"))
    if old is None:
        return None
    try:
        config = json.loads(old)
    except (ValueError, TypeError) as exc:
        raise cf.SyncError("hooks.json JSON 无效；未覆盖") from exc
    changed = False
    for group in hook_groups(config):
        if not isinstance(group, dict) or not isinstance(group.get("hooks", []), list):
            continue
        for handler in group.get("hooks", []):
            identity = hook_identity(f, handler)
            if identity is None:
                continue
            command = migrated_hook_command(f, handler, identity)
            changed |= handler["command"] != command
            handler["command"] = command
    return encoded(config) if changed else None


def transaction_value(tx):
    if (not isinstance(tx, dict) or type(tx.get("version")) is not int or tx["version"] != FORMAT
            or "previous" not in tx or not isinstance(tx.get("changes"), dict)):
        raise cf.SyncError("运行恢复记录对象或字段类型无效")
    pointer_value(tx.get("candidate"))
    if tx["previous"] is not None:
        pointer_value(tx["previous"])
    for key, item in tx["changes"].items():
        if not isinstance(item, dict) or set(item) != {"before", "after"}:
            raise cf.SyncError("运行恢复文件记录形状无效")
        for value in item.values():
            if not isinstance(value, dict) or set(value) not in ({"data"}, {"link"}):
                raise cf.SyncError("运行恢复文件值形状无效")
            if "link" in value:
                if key != "launcher" or not isinstance(value["link"], str):
                    raise cf.SyncError("运行恢复链接字段无效")
            elif value["data"] is not None:
                if not isinstance(value["data"], str):
                    raise cf.SyncError("运行恢复内容字段无效")
                try:
                    base64.b64decode(value["data"], validate=True)
                except ValueError as exc:
                    raise cf.SyncError("运行恢复内容编码无效") from exc
    return tx


def checkpoint(name):
    """Fault-injection seam for isolated tests; no environment-triggered production faults."""


def install(f, revision=None, expected_runtime=None, rollback=False, release_version=None):
    revision = revision or cf.git(f.repo, "rev-parse", "HEAD")
    if release_version is not None:
        expected_version = validate_release_version(release_version)
        if rollback:
            declared = declared_release_version(cf.read_file(version_path(f, revision) / "scripts/codex_framework.py"))
        else:
            declared = declared_release_version(cf.git(f.repo, "show", revision + ":scripts/codex_framework.py"))
        if declared != expected_version:
            raise cf.SyncError("目标核心产品版本不符；未准备或切换运行包")
    before_active = active(f)
    expected = expected_runtime if expected_runtime is not None else (before_active or {}).get("revision", "none")
    with f.lock():
        old = active(f)
        if ((old or {}).get("revision", "none")) != expected:
            return {"status": "superseded", "active_revision": (old or {}).get("revision")}
        if journal(f).exists():
            raise cf.SyncError("上次运行更新未完成；先 runtime-recover，不覆盖恢复点")
        if rollback:
            directory = version_path(f, revision)
            fingerprint = approved_packages(f).get(revision)
            if not fingerprint:
                raise cf.SyncError("回退目标不是此前已校验的运行包")
            manifest, snapshot = validate(directory, fingerprint)
        else:
            directory, manifest, snapshot = prepare(f, revision)
        import framework_refresh as refresh
        files = refresh.validate_snapshot(snapshot)
        results = f.reconcile(source=files)
        conflicts = [n for n, action in results if action == "conflict"]
        if conflicts:
            return {"status": "conflicts", "conflicts": conflicts, "active_revision": (old or {}).get("revision")}
        launcher = launcher_path(f)
        existing = capture(launcher)
        if "link" in existing and Path(existing["link"]).absolute() != f.repo / "scripts/codex_framework.py":
            raise cf.SyncError("launcher 指向未知位置；保留原入口")
        if ("data" in existing and existing["data"] is not None
                and not managed_loader_matches(f, base64.b64decode(existing["data"], validate=True), old)):
            raise cf.SyncError("launcher 为未知内容；未覆盖")
        new_active = {"version": FORMAT, "revision": revision, "manifest_sha256": digest(encoded(manifest)),
                      "source_repo": str(f.repo), "previous_revision": (old or {}).get("revision")}
        changes = {}
        def change(key, value):
            path = target(f, key)
            before, after = capture(path), file_value(value)
            if key == "launcher" and before != existing:
                raise cf.SyncError("launcher 在入口核对后改变；未覆盖")
            if before != after:
                changes[key] = {"before": before, "after": after}
        base = f.load_state()
        for name, action in results:
            if action == "update":
                change(name, files.get(name))
            if action in ("update", "same"):
                base[name] = files.get(name)
        state = f.read_state()
        overrides = [n for n, a in results if a == "keep-local"]
        state["base"] = {n: base64.b64encode(v).decode() if v is not None else None for n, v in sorted(base.items())}
        state["sync"] = {"status": "rolled-back" if rollback else "current", "route": "runtime",
                         "observed_revision": state.get("sync", {}).get("observed_revision", revision) if rollback else revision,
                         "applied_revision": revision, "conflicts": [], "local_overrides": overrides}
        change("cache", encoded(snapshot))
        change("state", encoded(state))
        change("launcher", prior_loader_bytes(f, new_active))
        hook = hook_update(f)
        if hook is not None:
            change("hook", hook)
        change("active", encoded(new_active))
        transaction = {"version": FORMAT, "previous": old, "candidate": new_active, "changes": changes}
        cf.write_file(journal(f), encoded(transaction))
        checkpoint("prepared")
        for key, item in changes.items():
            path = target(f, key)
            if capture(path) != item["before"]:
                raise cf.SyncError("更新期间内容改变，保留恢复点: " + key)
            path.parent.mkdir(parents=True, exist_ok=True)
            replace(path, item["after"], launcher=key == "launcher")
            checkpoint(key)
        checkpoint("switched")
        journal(f).unlink()
        source_overrides = cf.git(f.repo, "diff", "--name-only", revision, optional=True) if f.repo.exists() else None
        return {"status": "rolled-back" if rollback else "installed", "revision": revision,
                "release_version": declared_release_version(cf.read_file(directory / "scripts/codex_framework.py")),
                "source_overrides": [n for n in (source_overrides or "").splitlines()
                                     if cf.allowed(n) or n in {"scripts/" + v for v in MODULES}],
                "directory": str(directory), "changed": list(changes), "local_overrides": overrides,
                "hook_trust": "native-review-required" if hook is not None else "unchanged",
                "message": "新调用固定运行该版本；已运行的旧版本保留原物理目录"}


def recover(f):
    with f.lock():
        tx = load(journal(f))
        if tx is None:
            return {"status": "no-pending-update"}
        transaction_value(tx)
        if active(f) not in (tx["previous"], tx["candidate"]):
            raise cf.SyncError("恢复点版本或当前活动版本已改变；不覆盖其他更新")
        # Validate every destination before restoring anything.
        for key in tx["changes"]:
            target(f, key)
        conflicts = []
        for key, item in reversed(list(tx["changes"].items())):
            path = target(f, key)
            current = capture(path)
            if current == item["before"]:
                continue
            if current != item["after"]:
                conflicts.append(key)
                continue
            replace(path, item["before"], launcher=key == "launcher")
        if conflicts:
            return {"status": "recovery-conflicts", "conflicts": conflicts}
        journal(f).unlink()
        return {"status": "recovered", "active_revision": (tx["previous"] or {}).get("revision")}
