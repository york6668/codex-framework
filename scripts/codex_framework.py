#!/usr/bin/env python3
"""Distribute only the Codex framework; never commit, push, or copy memories."""

import argparse
import base64
import contextlib
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


REMOTE = "https://github.com/york6668/codex-framework.git"
BRANCH = "york-worktree/Codex框架共享"
SKILLS = (
    "york-codex-policy-maintenance", "york-cross-repo-change", "york-engineering", "york-java-test",
    "york-local-data", "york-log-query", "york-provider-docs", "york-safe-delivery",
    "york-task-alignment", "york-workstation",
)
DOCUMENTS = (
    ".codex/AGENTS.md", ".codex/model-defect-recovery.md",
    ".codex/memory-framework.md",
    ".codex/memory-settings.example.toml", ".codex/workstation/README.md",
)
UNKNOWN = object()


class SyncError(Exception):
    pass


def allowed(name):
    path = Path(name)
    if name != path.as_posix() or path.is_absolute() or any(p in (".", "..") for p in path.parts):
        return False
    if name in DOCUMENTS:
        return True
    return (len(path.parts) >= 4 and path.parts[:2] == (".agents", "skills")
            and path.parts[2] in SKILLS and path.suffix in (".md", ".yaml")
            and all(not p.startswith(".") for p in path.parts[3:]))


def safe_path(root, name):
    """Do not follow a symlink in either the source or the destination."""
    if Path(name).is_absolute() or ".." in Path(name).parts:
        raise SyncError("非法相对路径: " + name)
    root = Path(root).absolute()
    for part in (root, *root.parents):
        if part.is_symlink():
            raise SyncError("路径包含符号链接: " + str(part))
    target = root
    for part in Path(name).parts:
        target = target / part
        if target.is_symlink():
            raise SyncError("路径包含符号链接: " + str(target))
    return target


def read_file(path):
    if path.is_symlink():
        raise SyncError("拒绝读取符号链接: " + str(path))
    if not path.exists():
        return None
    if not path.is_file():
        raise SyncError("预期普通文件: " + str(path))
    return path.read_bytes()


def write_file(path, value):
    if path.is_symlink():
        raise SyncError("拒绝覆盖符号链接: " + str(path))
    if value is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = (path.stat().st_mode & 0o777) if path.exists() else 0o600
    fd, temporary = tempfile.mkstemp(prefix=".framework-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def git(repo, *args, optional=False, timeout=60):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    env["GCM_INTERACTIVE"] = "never"
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes -o ConnectTimeout=5 "
                   "-o StrictHostKeyChecking=yes -o UpdateHostKeys=no")
    try:
        result = subprocess.run(["git", "-C", str(repo), *args], env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SyncError("Git 操作未完成；没有应用本机文件") from exc
    if result.returncode and not optional:
        error = result.stderr.lower()
        reason = ("认证不可用" if any(s in error for s in
                  ("username", "authentication", "permission denied", "credential"))
                  else "SSH 主机信任缺失" if "host key" in error
                  else "网络、仓库或工作区不可用")
        raise SyncError("Git " + args[0] + " 失败：" + reason)
    return result.stdout.strip() if result.returncode == 0 else None


def decide(base, current, incoming):
    if current == incoming:
        return "same"
    if base is UNKNOWN:
        return "update" if current is None else "conflict"
    if current == base:
        return "update"
    if incoming == base:
        return "keep-local"
    return "conflict"


class Framework:
    def __init__(self, repo, home, codex_home=None):
        self.repo = Path(repo).absolute()
        self.home = Path(home).absolute()
        self.codex_home = Path(codex_home).absolute() if codex_home else self.home / ".codex"
        self.state_path = safe_path(self.codex_home, "framework-sync/state.json")

    def target(self, name):
        if not allowed(name):
            raise SyncError("不属于共享框架: " + name)
        if name.startswith(".codex/"):
            return safe_path(self.codex_home, name[len(".codex/"):])
        return safe_path(self.home, name)

    def collect(self, local=False):
        files = {}
        for name in DOCUMENTS:
            path = self.target(name) if local else safe_path(self.repo, name)
            value = read_file(path)
            if value is not None:
                files[name] = value
        for skill in SKILLS:
            name = ".agents/skills/" + skill
            root = safe_path(self.home if local else self.repo, name)
            if root.exists() and not root.is_dir():
                raise SyncError("Skill 目录不是目录: " + str(root))
            for path in sorted(root.rglob("*")):
                relative = name + "/" + path.relative_to(root).as_posix()
                if path.is_symlink():
                    raise SyncError("Skill 包含符号链接: " + str(path))
                if path.is_file() and allowed(relative):
                    files[relative] = read_file(safe_path(root, path.relative_to(root).as_posix()))
        if not local:
            required = set(DOCUMENTS) | {".agents/skills/" + s + "/SKILL.md" for s in SKILLS}
            missing = required - files.keys()
            if missing:
                raise SyncError("共享源缺少入口文件: " + ", ".join(sorted(missing)))
        return files

    def read_state(self):
        raw = read_file(safe_path(self.codex_home, "framework-sync/state.json"))
        if raw is None:
            return {"version": 1, "source": REMOTE, "base": {}}
        try:
            state = json.loads(raw)
            if state["version"] != 1 or state["source"] != REMOTE:
                raise ValueError("状态版本或来源不匹配")
            values = state["base"]
            if not isinstance(values, dict) or not all(allowed(n) for n in values):
                raise ValueError("状态包含非框架文件")
            for value in values.values():
                if value is not None:
                    base64.b64decode(value, validate=True)
            if not isinstance(state.get("sync", {}), dict):
                raise ValueError("同步状态格式错误")
            return state
        except (KeyError, TypeError, ValueError) as exc:
            raise SyncError("无法读取框架同步基线；未覆盖文件") from exc

    def load_state(self):
        return {n: base64.b64decode(v) if v is not None else None
                for n, v in self.read_state()["base"].items()}

    def save_state(self, values, sync=None):
        if not all(allowed(n) for n in values):
            raise SyncError("基线包含非框架文件")
        state = self.read_state()
        state["base"] = {
            n: base64.b64encode(v).decode("ascii") if v is not None else None
            for n, v in sorted(values.items())}
        if sync is not None:
            state["sync"] = sync
        destination = safe_path(self.codex_home, "framework-sync/state.json")
        encoded = (json.dumps(state, ensure_ascii=False, indent=2) + "\n").encode()
        if read_file(destination) != encoded:
            write_file(destination, encoded)

    @contextlib.contextmanager
    def lock(self):
        path = safe_path(self.codex_home, "framework-sync/lock")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise SyncError("本设备已有框架同步操作；请等该操作结束") from exc
            yield

    def reconcile(self, apply=False, export=False, source=None, _locked=False):
        manager = self.lock() if apply and not _locked else contextlib.nullcontext()
        with manager:
            base = self.load_state()
            source = self.collect() if source is None else source
            required = set(DOCUMENTS) | {".agents/skills/" + s + "/SKILL.md" for s in SKILLS}
            if required - source.keys() or not all(allowed(n) for n in source):
                raise SyncError("共享源不完整或包含范围外文件")
            if export and not base:
                raise SyncError("请先安装并核对框架，再导出本机修改")
            local = self.collect(local=True) if export else {}
            if export:
                required = set(DOCUMENTS) | {".agents/skills/" + s + "/SKILL.md" for s in SKILLS}
                if required - local.keys():
                    raise SyncError("本机缺少必需入口；未导出删除，请先恢复或核对该入口")
            names = set(source) | set(base) | set(local)
            results = []
            for name in sorted(names):
                destination = safe_path(self.repo, name) if export else self.target(name)
                current = read_file(destination)
                incoming = local.get(name) if export else source.get(name)
                action = decide(base.get(name, UNKNOWN), current, incoming)
                if apply and action == "update":
                    # Editors do not take our lock; retain a change noticed since the comparison.
                    destination = safe_path(self.repo, name) if export else self.target(name)
                    if read_file(destination) != current:
                        action = "conflict"
                    else:
                        write_file(destination, incoming)
                if apply and not export and action in ("same", "update"):
                    base[name] = incoming
                results.append((name, action))
            if apply and not export:
                self.save_state(base)
            # Export edits the review checkout only; it is not a published baseline.
            return results

    def launcher(self):
        import framework_runtime as runtime
        path = runtime.launcher_path(self)
        current = runtime.active(self)
        if not current or read_file(path) != runtime.loader_bytes(self):
            raise SyncError("先 install 固定运行包；不再生成可变源码链接")
        runtime.validate(runtime.version_path(self, current["revision"]), current["manifest_sha256"])
        return path

    def pull(self):
        remote = git(self.repo, "config", "--get", "remote.origin.url")
        if remote not in (REMOTE, REMOTE.removesuffix(".git"),
                          "git@github.com:york6668/codex-framework.git"):
            raise SyncError("origin 不是 york6668/codex-framework；没有拉取")
        if git(self.repo, "branch", "--show-current") != BRANCH:
            raise SyncError("请在发布分支 " + BRANCH + " 上拉取；不会自动切换工作分支")
        git(self.repo, "fetch", "--no-tags", "origin", "refs/heads/" + BRANCH)
        # Git retains unrelated edits and refuses an overwrite or divergent history.
        git(self.repo, "merge", "--ff-only", "FETCH_HEAD")


def report(results):
    counts = {k: sum(a == k for _, a in results)
              for k in ("same", "update", "keep-local", "conflict")}
    for name, action in results:
        if action != "same":
            print(action + " " + name)
    print(" ".join(k + "=" + str(v) for k, v in counts.items()))
    return 2 if counts["conflict"] else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--home", type=Path, help="目标用户目录；显式指定时不使用环境中的 CODEX_HOME")
    parser.add_argument("--codex-home", type=Path, help="显式指定目标 Codex home")
    parser.add_argument("command", choices=("status", "install", "apply", "pull", "export",
                        "sync-status", "refresh", "install-hook", "session-start", "check",
                        "workstation-check", "workstation-entry", "runtime-recover", "runtime-rollback", "task"))
    parser.add_argument("--apply", action="store_true", help="pull 后应用；否则仅预览差异")
    parser.add_argument("--snapshot", type=Path, help="原生连接取得的完整共享内容快照")
    parser.add_argument("--revision", help="原生连接刚读取的发布提交 SHA")
    parser.add_argument("--cloud-error", help="云端入口不可用的简短原因；随后使用本机 Git")
    parser.add_argument("--expected-revision", help="访问远端前 sync-status 的 observed_revision；无版本用 none")
    parser.add_argument("--expected-runtime", help="安装或回退前观察到的运行版本；首次为 none")
    args = parser.parse_args(argv)
    if args.apply and args.command != "pull":
        parser.error("--apply 仅用于 pull")
    if args.command == "check":
        from execution_check import dispatch
        return dispatch()
    if args.command == "workstation-check":
        from workstation_check import dispatch
        return dispatch()
    codex_home = args.codex_home or (os.environ.get("CODEX_HOME") if args.home is None else None)
    if args.command == "task":
        from task_runtime import dispatch
        return dispatch(codex_home or (args.home or Path.home()) / ".codex")
    if args.command == "workstation-entry":
        from workstation_entry import dispatch
        return dispatch(codex_home or (args.home or Path.home()) / ".codex")
    import framework_runtime as runtime
    try:
        framework = Framework(args.repo or runtime.default_repo(),
                              args.home or Path.home(), codex_home)
        import framework_refresh as refresh
        if args.command in ("install", "runtime-rollback", "runtime-recover"):
            if args.command == "runtime-recover":
                output = runtime.recover(framework)
            else:
                if args.command == "runtime-rollback" and not args.revision:
                    raise SyncError("runtime-rollback 要求 --revision 指定已保留版本")
                output = runtime.install(framework, args.revision, args.expected_runtime,
                                         rollback=args.command == "runtime-rollback")
            print(json.dumps(output, ensure_ascii=False, indent=2))
            return 2 if output.get("conflicts") else 0
        if args.command in ("sync-status", "refresh", "install-hook", "session-start"):
            return refresh.dispatch(framework, args)
        if args.command == "pull":
            expected_revision = refresh.sync_status(framework)["expected_revision"]
            framework.pull()
            if args.apply:
                snapshot = refresh.snapshot_at_revision(framework, git(framework.repo, "rev-parse", "HEAD"))
                output = refresh.refresh(framework, snapshot=snapshot, expected_revision=expected_revision,
                                         route="local")
                print(json.dumps(output, ensure_ascii=False, indent=2))
                return 2 if output.get("conflicts") else 0
        applying = args.command in ("install", "apply", "export") or args.apply
        # A connector refresh may be newer than the local source-code checkout.
        # Default status/apply compare against that published payload, not stale code.
        source = refresh.cached_files(framework) if args.repo is None and args.command in ("status", "apply") else None
        results = framework.reconcile(apply=applying, export=args.command == "export", source=source)
        code = report(results)
        if args.command == "export":
            print("仅更新本地审核仓库；未暂存、提交或推送")
        elif applying:
            print("未修改 config.toml、历史记忆或会话；新会话读取更新后的框架")
        return code
    except (SyncError, OSError) as exc:
        print("codex-framework: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.modules["codex_framework"] = sys.modules[__name__]
    sys.exit(main())
