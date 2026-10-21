"""Task-driven refresh. Native repository access stays in the calling Codex task."""

import hashlib
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

import codex_framework as cf


SHA = re.compile(r"[0-9a-f]{40}\Z")
REFERENCE = ".agents/skills/york-codex-policy-maintenance/references/framework-sync.md"
HOOK_LABEL = "检查 Codex 共享框架版本"


def blob_sha(value):
    return hashlib.sha1(b"blob " + str(len(value)).encode() + b"\0" + value).hexdigest()


def validate_snapshot(snapshot):
    """Check completeness before interpreting absent files as upstream deletions."""
    try:
        if (snapshot["source"] != cf.REMOTE or snapshot["branch"] != cf.BRANCH
                or not SHA.fullmatch(snapshot["revision"]) or snapshot["truncated"] is not False):
            raise ValueError("来源、分支、版本或完整性不匹配")
        manifest = {}
        for entry in snapshot["tree"]:
            name = entry["path"]
            if not cf.allowed(name):
                continue
            if name in manifest or entry["type"] != "blob" or entry["mode"] != "100644":
                raise ValueError("共享内容必须是唯一普通文本文件")
            manifest[name] = entry["sha"]
        values = snapshot["files"]
        required = set(cf.DOCUMENTS) | {".agents/skills/" + s + "/SKILL.md" for s in cf.SKILLS}
        if set(values) != set(manifest) or required - values.keys():
            raise ValueError("快照缺少文件或包含范围外文件")
        files = {name: value.encode("utf-8") for name, value in values.items()}
        if any(blob_sha(value) != manifest[name] for name, value in files.items()):
            raise ValueError("快照正文与仓库文件不一致")
        return files
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise cf.SyncError("原生仓库快照无效或不完整；未应用该快照") from exc


def cache_path(framework):
    return cf.safe_path(framework.codex_home, "framework-sync/upstream.json")


def cached_files(framework):
    raw = cf.read_file(cache_path(framework))
    if raw is None:
        return None
    try:
        snapshot = json.loads(raw)
    except ValueError as exc:
        raise cf.SyncError("已缓存的共享版本无法读取") from exc
    return validate_snapshot(snapshot)


def sync_status(framework):
    import framework_runtime as runtime
    try:
        state = runtime.sync_state(framework)
        binding = runtime.describe(framework, state)
    except (cf.SyncError, OSError) as exc:
        return {"status": "runtime-unavailable", "document_status": "unknown",
                "source": cf.REMOTE, "branch": cf.BRANCH, "expected_revision": "none",
                "runtime": {"status": "unknown", "reason": str(exc)}}
    result = dict(state, source=cf.REMOTE, branch=cf.BRANCH,
                  expected_revision=state.get("observed_revision") or "none", runtime=binding,
                  document_status=state.get("status", "unknown"))
    if binding["status"] not in ("matched", "unmanaged"):
        result["status"] = "runtime-" + binding["status"]
    return result


def local_snapshot(framework):
    """Fetch the publishing branch without changing the user's checkout or index."""
    remote = cf.git(framework.repo, "config", "--get", "remote.origin.url")
    if remote not in (cf.REMOTE, cf.REMOTE.removesuffix(".git"),
                      "git@github.com:york6668/codex-framework.git"):
        raise cf.SyncError("本机 origin 未指向共享框架")
    cf.git(framework.repo, "fetch", "--no-tags", "origin", "refs/heads/" + cf.BRANCH, timeout=10)
    revision = cf.git(framework.repo, "rev-parse", "FETCH_HEAD")
    return snapshot_at_revision(framework, revision)


def snapshot_at_revision(framework, revision):
    tree = []
    files = {}
    for line in cf.git(framework.repo, "-c", "core.quotepath=false", "ls-tree", "-r", revision).splitlines():
        identity, name = line.split("\t", 1)
        mode, kind, sha = identity.split()
        if cf.allowed(name):
            tree.append({"path": name, "mode": mode, "type": kind, "sha": sha})
            # git() strips whitespace, so read the blob bytes separately.
            value = subprocess.run(["git", "-C", str(framework.repo), "cat-file", "blob", sha],
                                   capture_output=True, timeout=10, check=True).stdout
            files[name] = value.decode("utf-8")
    snapshot = {"source": cf.REMOTE, "branch": cf.BRANCH, "revision": revision,
                "truncated": False, "tree": tree, "files": files}
    validate_snapshot(snapshot)
    return snapshot


def save_sync(framework, state):
    framework.save_state(framework.load_state(), sync=state)


def failure(framework, previous, cloud_error, local_error):
    state = dict(previous, status="pending-user", cloud_error=cloud_error[:400],
                 local_error=local_error[:400])
    notify = state != previous
    save_sync(framework, state)
    return dict(state, notify=notify, message="云端与本机入口均不可用，待用户处理；继续使用已安装规范")


def refresh(framework, *, snapshot=None, revision=None, cloud_error=None, expected_revision, route="cloud"):
    """One source attempt per route. The lock spans fallback fetch and file application."""
    with framework.lock():
        previous = framework.read_state().get("sync", {})
        actual = previous.get("observed_revision") or "none"
        if actual != expected_revision:
            return dict(previous, status="superseded", notify=False,
                        message="其他同步已更新本机版本；读取现用规范，未覆盖")
        if snapshot is not None:
            try:
                files = validate_snapshot(snapshot)
                revision = snapshot["revision"]
            except cf.SyncError as exc:
                cloud_error = str(exc)
                snapshot = None
        if snapshot is None and not cloud_error:
            if not revision or not SHA.fullmatch(revision):
                raise cf.SyncError("先使用原生连接读取版本，或提供该入口不可用的具体原因")
            if previous.get("applied_revision") != revision or previous.get("conflicts"):
                return dict(previous, status="snapshot-required", revision=revision, notify=False)
        if cloud_error:
            route = "local"
            try:
                snapshot = local_snapshot(framework)
                files = validate_snapshot(snapshot)
                revision = snapshot["revision"]
            except (cf.SyncError, OSError, ValueError, subprocess.SubprocessError) as exc:
                reason = str(exc) if isinstance(exc, cf.SyncError) else "本机 Git 内容读取失败或超时"
                return failure(framework, previous, cloud_error, reason)
        if previous.get("applied_revision") == revision and not previous.get("conflicts"):
            state = dict(previous, status="current", route=route)
            state.pop("cloud_error", None)
            state.pop("local_error", None)
            save_sync(framework, state)
            return dict(state, changed=[], notify=False)
        # Keep the immutable incoming payload available for conflict review and status.
        encoded = (json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n").encode()
        if cf.read_file(cache_path(framework)) != encoded:
            cf.write_file(cache_path(framework), encoded)
        results = framework.reconcile(apply=True, source=files, _locked=True)
        conflicts = [n for n, action in results if action == "conflict"]
        changed = [n for n, action in results if action == "update"]
        local = [n for n, action in results if action == "keep-local"]
        state = {"status": "conflicts" if conflicts else "current", "route": route,
                 "observed_revision": revision,
                 "applied_revision": previous.get("applied_revision") if conflicts else revision,
                 "conflicts": conflicts, "local_overrides": local}
        save_sync(framework, state)
        return dict(state, changed=changed, notify=bool(conflicts and state != previous))


def install_hook(framework):
    """Merge only our handler. Native Codex trust is deliberately left to its UI."""
    import framework_runtime as runtime
    command = runtime.stable_command(framework)
    path = cf.safe_path(framework.codex_home, "hooks.json")
    raw = cf.read_file(path)
    try:
        config = json.loads(raw) if raw is not None else {}
        groups = runtime.hook_groups(config)
        found = False
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks", []), list):
                continue
            kept, seen = [], set()
            for handler in group.get("hooks", []):
                identity = runtime.hook_identity(framework, handler)
                if identity is None:
                    kept.append(handler)
                    continue
                found = True
                handler = dict(handler, command=runtime.migrated_hook_command(framework, handler, identity))
                # Only equivalent current-target handlers within the same matcher,
                # with the same runner and non-command attributes, are duplicates.
                key = json.dumps({"runner": identity["runner"], "repo": identity["repo"],
                                  "target": identity["target"],
                                  "properties": {k:v for k,v in handler.items() if k != "command"}}, sort_keys=True)
                if key not in seen:
                    kept.append(handler)
                    seen.add(key)
            group["hooks"] = kept
        if not found:
            handler = {"type": "command", "command": command, "timeout": 5,
                       "statusMessage": HOOK_LABEL, "additionalContextLimit": 1800}
            groups.append({"matcher": "^(startup|resume|clear)$", "hooks": [handler]})
    except (TypeError, ValueError, AttributeError) as exc:
        raise cf.SyncError("现有 hooks.json 格式无法安全合并；未覆盖") from exc
    encoded = (json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode()
    if raw != encoded:
        if cf.read_file(path) != raw:
            raise cf.SyncError("hooks.json 在安装期间被修改；保留当前内容")
        cf.write_file(path, encoded)
    commands = [h["command"] for g in groups if isinstance(g, dict) and isinstance(g.get("hooks", []), list)
                for h in g.get("hooks", []) if runtime.hook_identity(framework, h) is not None]
    return {"path": str(path), "trust": "native-review-required",
            "command": commands[0] if commands else command, "commands": commands}


def session_start(framework, event):
    if event.get("hook_event_name") != "SessionStart" or event.get("source") not in ("startup", "resume", "clear"):
        return {}
    # This hook never performs network I/O, runs another model, or decides permissions.
    import framework_runtime as runtime
    command = shlex.join([sys.executable, str(runtime.launcher_path(framework)),
                          "--home", str(framework.home),
                          "--codex-home", str(framework.codex_home)])
    context = ("Codex 共享框架启动检查：遵循当前用户指令和更高优先级约束。"
               "框架命令使用绝对前缀 " + command + "，其后接子命令，不依赖 PATH。"
               "摘要异常或未知时先报告具体状态、归属限制和可执行的原任务核对；"
               "已收到摘要无需重复调用观察入口。"
               "摘要缺席用该前缀执行 workstation-entry；未知保留未知，不递归枚举工作站。"
               "原任务记录从原生入口读，节点原件按清单有界读取。"
               "新建、恢复或清空任务先执行 sync-status，再从当前可用 GitHub 原生入口核对 "
               "york6668/codex-framework 的 " + cf.BRANCH + " 发布分支。"
               "current 仅说明已观察状态；分别核发布、部署正文、运行程序与本轮实际读取。"
               "需要刷新、入口故障或版本不明时读 " + str(framework.home / REFERENCE) +
               "；常规启动不解析同步器源码。版本未变且部署完整不重复安装；"
               "有更新保护本机修改，更新后重读全局及已使用 Skill。"
               "同步失败保留现用版本并继续无依赖工作；普通追加和压缩不联网。"
               "复杂或结果不明可执行 check 的 resume/feedback/close_issue；ready 不代表完成。"
               "缺陷修正后先手动从本地正常入口复测。")
    try:
        import workstation_entry
        observation = workstation_entry.summary(framework.codex_home)
    except Exception:
        observation = "工作站观察: 未知(观察入口失败)；检查节点来源清单与原任务。"
    limit = max(0, 1790 - len(context.encode("utf-8")))
    observation = observation.encode("utf-8")[:limit].decode("utf-8", "ignore")
    context = observation + " " + context
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": context}}


def dispatch(framework, args):
    if args.command == "sync-status":
        output = sync_status(framework)
    elif args.command == "install-hook":
        with framework.lock():
            output = install_hook(framework)
    elif args.command == "session-start":
        try:
            output = session_start(framework, json.load(sys.stdin))
        except (ValueError, TypeError, AttributeError):
            output = {}
    else:
        if not args.expected_revision:
            raise cf.SyncError("refresh 需要访问远端前 sync-status 返回的 --expected-revision")
        selected = sum(bool(v) for v in (args.snapshot, args.revision, args.cloud_error))
        if selected != 1:
            raise cf.SyncError("refresh 使用 --snapshot、--revision 或 --cloud-error 之一")
        snapshot, error = None, args.cloud_error
        if args.snapshot:
            try:
                snapshot = json.loads(cf.read_file(args.snapshot))
            except (OSError, ValueError, TypeError) as exc:
                error = "原生连接快照文件无法读取或格式错误"
        output = refresh(framework, snapshot=snapshot, revision=args.revision,
                         cloud_error=error, expected_revision=args.expected_revision)
    print(json.dumps(output, ensure_ascii=False, indent=2))
    # Pending/conflicted sync is a report, never a prompt-blocking hook exit code.
    return 0
