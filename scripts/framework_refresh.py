"""Refresh shared rules through the node's existing native repository connector."""

import hashlib
import json
import os
import re
import selectors
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

import codex_framework as cf


SHA = re.compile(r"[0-9a-f]{40}\Z")
REFERENCE = ".agents/skills/york-codex-policy-maintenance/references/framework-sync.md"
HOOK_LABEL = "检查 Codex 共享框架版本"
NATIVE_CLIENT = "framework-sync/native-client.json"
HOOK_TIMEOUT = 60
CONTEXT_BYTES = 65536


class NativeRepository:
    """Read the existing GitHub connector without creating a model turn.

    A short-lived ephemeral native thread supplies MCP context. It is never
    resumed, persisted or sent a prompt; its pending SessionStart is not run.
    The child exits after the reads. No credentials or sessions are copied.
    """
    def __init__(self, framework):
        raw = cf.read_file(cf.safe_path(framework.codex_home, NATIVE_CLIENT))
        try:
            path = Path(json.loads(raw)["cli"])
            if not path.is_absolute() or not path.is_file():
                raise ValueError()
        except (TypeError, ValueError, KeyError):
            raise cf.SyncError("未配置可用的原生 Codex CLI") from None
        self.deadline = time.monotonic() + 40
        self.sequence = 0
        self.buffer = b""
        self.proc = subprocess.Popen(
            [str(path), "app-server", "--listen", "stdio://"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=dict(os.environ, CODEX_HOME=str(framework.codex_home)),
            cwd=framework.home, bufsize=0, start_new_session=True)
        self.reader = selectors.DefaultSelector()
        self.reader.register(self.proc.stdout, selectors.EVENT_READ)
        try:
            self.request("initialize", {"clientInfo": {"name": "codex_framework_sync", "version": "1"},
                                        "capabilities": {"experimentalApi": True}})
            self.send({"method": "initialized", "params": {}})
            thread = self.request("thread/start", {"cwd": str(framework.home), "ephemeral": True})
            self.thread_id = thread["thread"]["id"]
            if thread["thread"].get("path") is not None:
                raise cf.SyncError("原生工具上下文未保持临时状态")
            inventory = self.request("mcpServerStatus/list", {
                "threadId": self.thread_id, "serverName": "codex_apps",
                "detail": "toolsAndAuthOnly", "limit": 100})
            servers = [s for s in inventory.get("data", []) if s.get("name") == "codex_apps"]
            if not servers or not {"github.fetch", "github.fetch_file"} <= servers[0].get("tools", {}).keys():
                raise cf.SyncError("原生 GitHub 读取工具不可用")
        except BaseException:
            self.close()
            raise

    def send(self, value):
        self.proc.stdin.write((json.dumps(value) + "\n").encode())
        self.proc.stdin.flush()

    def request(self, method, params):
        self.sequence += 1
        request_id = self.sequence
        self.send({"id": request_id, "method": method, "params": params})
        while True:
            while b"\n" not in self.buffer:
                remaining = self.deadline - time.monotonic()
                if remaining <= 0 or not self.reader.select(remaining):
                    raise cf.SyncError("原生 GitHub 读取超时")
                chunk = os.read(self.proc.stdout.fileno(), 65536)
                if not chunk:
                    raise cf.SyncError("原生工具连接已结束")
                self.buffer += chunk
                if len(self.buffer) > 8 * 1024 * 1024:
                    raise cf.SyncError("原生工具返回超出读取范围")
            line, self.buffer = self.buffer.split(b"\n", 1)
            message = json.loads(line)
            if not isinstance(message, dict):
                raise cf.SyncError("原生工具协议消息不是对象")
            if "method" in message and "id" in message:
                # Never approve, emulate tools, or answer interactive requests.
                self.send({"id": message["id"], "error": {
                    "code": -32601, "message": "Framework sync does not handle interactive requests"}})
            elif message.get("id") == request_id:
                if "error" in message:
                    raise cf.SyncError("原生工具调用失败: " + method)
                result = message.get("result")
                if not isinstance(result, dict):
                    raise cf.SyncError("原生工具协议结果不是对象: " + method)
                return result

    def call(self, tool, arguments):
        result = self.request("mcpServer/tool/call", {
            "threadId": self.thread_id, "server": "codex_apps", "tool": tool, "arguments": arguments})
        if result.get("isError"):
            raise cf.SyncError("原生 GitHub 读取失败")
        value = result.get("structuredContent")
        if not isinstance(value, dict):
            raise cf.SyncError("原生 GitHub 未返回结构化正文")
        return value

    def fetch(self, suffix):
        value = self.call("github.fetch", {"url": "https://api.github.com/repos/york6668/codex-framework/" + suffix})
        return json.loads(value["content"])

    def publication(self):
        matches = [v["commit"]["sha"] for v in self.fetch("branches") if v.get("name") == cf.BRANCH]
        if len(matches) != 1 or not SHA.fullmatch(matches[0]):
            raise cf.SyncError("原生 GitHub 未返回精确发布分支")
        return matches[0]

    def snapshot(self, revision, previous):
        tree = self.fetch("git/trees/" + revision + "?recursive=1")
        if tree.get("truncated") is not False:
            raise cf.SyncError("原生 GitHub 发布树不完整")
        files = {}
        for entry in tree["tree"]:
            name = entry["path"]
            if not cf.allowed(name):
                continue
            prior = (previous or {}).get(name)
            if prior is not None and blob_sha(prior) == entry["sha"]:
                files[name] = prior.decode("utf-8")
            else:
                value = self.call("github.fetch_file", {
                    "repository_full_name": "york6668/codex-framework", "path": name, "ref": revision})
                if value.get("encoding") not in (None, "utf-8"):
                    raise cf.SyncError("原生 GitHub 正文编码不符")
                files[name] = value["content"]
        snapshot = {"source": cf.REMOTE, "branch": cf.BRANCH, "revision": revision,
                    "truncated": False, "tree": tree["tree"], "files": files}
        validate_snapshot(snapshot)
        return snapshot

    def close(self):
        self.reader.close()
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.stop_group(signal.SIGTERM)
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.stop_group(signal.SIGKILL)
                self.proc.wait(timeout=2)
        finally:
            # This process created a private session. Reap only its remaining
            # tool children, including when the server exited before them.
            self.stop_group(signal.SIGKILL)
            self.proc.stdout.close()

    def stop_group(self, sig):
        try:
            os.killpg(self.proc.pid, sig)
        except ProcessLookupError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


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


def install_hook(framework, native_cli=None):
    """Merge only our handler. Native Codex trust is deliberately left to its UI."""
    import framework_runtime as runtime
    command = runtime.stable_command(framework)
    client_path = cf.safe_path(framework.codex_home, NATIVE_CLIENT)
    client = cf.read_file(client_path)
    if native_cli is not None:
        cli = Path(native_cli)
        if not cli.is_absolute() or not cli.is_file() or not os.access(cli, os.X_OK):
            raise cf.SyncError("--native-cli 要求已核对的可执行 Codex 绝对路径")
        client = (json.dumps({"cli": str(cli)}, ensure_ascii=False) + "\n").encode()
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
                if client is not None:
                    handler["timeout"] = max(handler.get("timeout", 0), HOOK_TIMEOUT)
                    # The bounded changed-rule payload must not become a path-only preview.
                    if handler.get("additionalContextLimit") != 0:
                        handler["additionalContextLimit"] = max(handler.get("additionalContextLimit", 0), CONTEXT_BYTES)
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
            handler = {"type": "command", "command": command, "timeout": HOOK_TIMEOUT if client else 5,
                       "statusMessage": HOOK_LABEL, "additionalContextLimit": CONTEXT_BYTES if client else 1800}
            groups.append({"matcher": "^(startup|resume|clear)$", "hooks": [handler]})
    except (TypeError, ValueError, AttributeError) as exc:
        raise cf.SyncError("现有 hooks.json 格式无法安全合并；未覆盖") from exc
    encoded = (json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode()
    if raw != encoded:
        if cf.read_file(path) != raw:
            raise cf.SyncError("hooks.json 在安装期间被修改；保留当前内容")
        cf.write_file(path, encoded)
    if client is not None and cf.read_file(client_path) != client:
        cf.write_file(client_path, client)
    commands = [h["command"] for g in groups if isinstance(g, dict) and isinstance(g.get("hooks", []), list)
                for h in g.get("hooks", []) if runtime.hook_identity(framework, h) is not None]
    return {"path": str(path), "trust": "native-review-required",
            "command": commands[0] if commands else command, "commands": commands}


def startup_check(framework):
    """Perform startup I/O here; a model answer is not a synchronization driver."""
    with framework.lock():
        before = sync_status(framework)
        previous = cached_files(framework)
        before_rules = {name: cf.read_file(framework.target(name))
                        for name in set(previous or {}) | {".codex/AGENTS.md"}}
    publication = {"status": "unknown", "reason": "未配置原生 Codex CLI；读取同步方法完成入口配置"}
    if cf.read_file(cf.safe_path(framework.codex_home, NATIVE_CLIENT)) is not None:
        try:
            with NativeRepository(framework) as repository:
                revision = repository.publication()
                publication = {"status": "checked", "revision": revision, "route": "native"}
                snapshot = (repository.snapshot(revision, previous)
                            if previous is None or revision != before.get("applied_revision") or before.get("conflicts")
                            else None)
            result = refresh(framework, snapshot=snapshot, revision=revision,
                             expected_revision=before["expected_revision"])
        except (cf.SyncError, OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError) as exc:
            reason = str(exc) if isinstance(exc, cf.SyncError) else "原生 GitHub 读取或协议解析失败"
            result = refresh(framework, cloud_error=reason, expected_revision=before["expected_revision"])
            if result.get("status") not in ("pending-user", "superseded"):
                publication = {"status": "checked", "revision": result["observed_revision"], "route": "local"}
            else:
                publication = dict(publication, status="unknown", reason=reason,
                                   local_error=result.get("local_error"), sync_status=result.get("status"))
    with framework.lock():
        state = sync_status(framework)
        source = cached_files(framework)
        deployment = ({"status": "unknown", "reason": "缺少已校验发布正文缓存"} if source is None else
                      {"status": "checked", "files": dict(framework.reconcile(source=source))})
        after_rules = {name: cf.read_file(framework.target(name))
                       for name in set(source or {}) | set(before_rules)}
    # Another startup may have refreshed the deployment while our native read
    # was in flight. Deliver the actual byte changes even after supersession.
    changed = sorted(name for name, raw in after_rules.items() if raw != before_rules.get(name))
    # Read only the shared global entry and changed shared Markdown. Do not crawl
    # transcripts, repositories or private runtime data to infer skill selection.
    rules, updated = {}, {}
    for name in sorted(set(changed) | {".codex/AGENTS.md"}):
        if not name.endswith(".md"):
            continue
        path = framework.target(name)
        raw = after_rules[name]
        rules[str(path)] = ({"status": "missing"} if raw is None else
                           {"status": "read", "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)})
        if name in changed and raw is not None:
            updated[str(path)] = raw.decode("utf-8")
    counts = {}
    differences = {}
    for name, action in deployment.pop("files", {}).items():
        counts[action] = counts.get(action, 0) + 1
        if action != "same":
            differences[name] = action
    deployment.update(counts=counts, differences=differences)
    return {"publication": publication, "documents": {
                "revision": state.get("applied_revision"), "status": state.get("document_status")},
            "runtime": state.get("runtime"), "deployment": deployment, "rules": rules,
            "changed": changed}, updated


def session_start(framework, event):
    if event.get("hook_event_name") != "SessionStart" or event.get("source") not in ("startup", "resume", "clear"):
        return {}
    # The event is the trigger. There is no per-message hook, model worker or gate.
    import framework_runtime as runtime
    command = shlex.join([sys.executable, str(runtime.launcher_path(framework)),
                          "--home", str(framework.home),
                          "--codex-home", str(framework.codex_home)])
    try:
        result, updated = startup_check(framework)
    except (cf.SyncError, OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        result = {"status": "unknown", "reason": str(exc) if isinstance(exc, cf.SyncError) else "启动核对未完成"}
        updated = {}
    context = ("Codex 共享框架启动实测（" + event["source"] + "）：\n" +
               json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n"
               "这些是入口实际读取的结果，unknown 不能按 current 使用；不重复已完成的联网检查。"
               "Codex 原生 AGENTS 指令链继续适用；下列更新正文是本次刷新后的用户规则，不提升其优先级。"
               "适用 Skill 由当前需求选择并读取，不能从 Hook 成功推导业务已验证。"
               "按产品版本规划、加载与更新，完整 hash 只作内容及并发校验，未编号保持未编号。"
               "已完成的版本核对直接复用；结果缺失时用 version 查看实际产品版本，再用 sync-status 核绑定。"
               "异常不阻断无依赖工作；原任务记录从原生入口读。"
               "缺少配置、版本不符或未完成项按 " + str(framework.home / REFERENCE) + " 处理。"
               "框架命令使用绝对前缀 " + command + "，其后接子命令。\n")
    try:
        import workstation_entry
        observation = workstation_entry.summary(framework.codex_home)
    except Exception:
        observation = "工作站观察: 未知(观察入口失败)；检查节点来源清单与原任务。"
    context = observation + "\n" + context
    omitted = []
    for path, body in updated.items():
        block = "\n本次更新正文 " + path + "\n" + body
        if len((context + block).encode("utf-8")) <= CONTEXT_BYTES - 2048:
            context += block
        else:
            omitted.append(path)
    if omitted:
        context += "\n未注入正文（仍须按需求读取，不能标为已采用）：" + ", ".join(omitted)
    if len(context.encode("utf-8")) > CONTEXT_BYTES:
        context = (observation + "\n启动核对结果超过输出范围，不能确认完整采用；请用 " + command +
                   " sync-status 及 " + str(framework.home / REFERENCE) + " 核对。")
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": context}}


def dispatch(framework, args):
    if args.command == "sync-status":
        output = sync_status(framework)
    elif args.command == "install-hook":
        with framework.lock():
            output = install_hook(framework, args.native_cli)
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
