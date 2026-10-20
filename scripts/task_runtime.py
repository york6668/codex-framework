"""Durable execution ownership for tasks run through the framework task entry.

Native worker completion is never acceptance. Updates use a per-task lock and
revision compare-and-swap; no scheduler, credential manager or permission grant.
"""
import copy
from contextlib import contextmanager
import fcntl
import functools
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import stat
import selectors
import signal
import time
import uuid

import codex_framework as cf
import task_coordination as coordination

LIMIT = 262144
ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,95}\Z")
SHA = re.compile(r"[0-9a-f]{64}\Z")
SCOPE_SECONDS = 2
FILE_SECONDS = 5
FILE_BYTES = 268435456
COMMAND_OUTPUT_BYTES = 8388608
_IO_CHILD = False
_IO_REPORT = None
_IO_DEPTH = 0
_SCOPE_FAILURES = {}


class TaskError(cf.SyncError):
    pass


class RevisionConflict(TaskError):
    pass


class IOBudgetError(TaskError):
    def __init__(self, reason, report):
        super().__init__(reason)
        self.report = report


def digest(value):
    return hashlib.sha256(value).hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def require(condition, reason):
    if not condition:
        raise TaskError(reason)


def text(value):
    return isinstance(value, str) and 0 < len(value.strip()) <= 12000


def io_progress(phase, **facts):
    if _IO_REPORT is not None:
        os.write(_IO_REPORT, (json.dumps({"type": "progress", "phase": phase, **facts}) + "\n").encode())


def io_call(function):
    @functools.wraps(function)
    def call(*args,**kwargs):
        global _IO_DEPTH
        if _IO_DEPTH==0:_SCOPE_FAILURES.clear()
        _IO_DEPTH+=1
        try:return function(*args,**kwargs)
        finally:
            _IO_DEPTH-=1
            if _IO_DEPTH==0:_SCOPE_FAILURES.clear()
    return call


def isolated_io(operation, function, seconds):
    """Bound the coordinator even if a target filesystem syscall stops replying.

    POSIX CLI helper, not an OS access sandbox. Children close inherited state
    locks/native pipes; cleanup targets only their new process group, before
    reaping its leader. An unreaped kernel-stalled child is reported as unknown.
    """
    if _IO_CHILD:
        return function(), {}
    require(signal.getsignal(signal.SIGCHLD)==signal.SIG_DFL,'validation adapter requires ordinary child-reaping semantics')
    started = time.monotonic()
    read_fd, write_fd = os.pipe()
    try:
        pid = os.fork()
    except OSError:
        os.close(read_fd);os.close(write_fd)
        raise
    if pid == 0:
        global _IO_REPORT
        globals()["_IO_CHILD"] = True
        try:
            os.setsid()
            _IO_REPORT = write_fd
            for name in os.listdir('/dev/fd'):
                fd = int(name)
                if fd != write_fd:
                    try:os.close(fd)
                    except OSError:pass
            null = os.open('/dev/null', os.O_RDWR)
            for fd in (0,1,2):os.dup2(null,fd)
            if null > 2:os.close(null)
            os.write(write_fd, (json.dumps({"type":"started","pid":os.getpid(),"group":os.getpgrp()})+'\n').encode())
            def expired(signum, frame):
                raise IOBudgetError('validation_io_timeout',{'error_type':'IOBudgetError','validator_possible_effects':operation=='command'})
            signal.signal(signal.SIGALRM,expired);signal.setitimer(signal.ITIMER_REAL,seconds)
            try:
                result = {"ok": True, "value": function()}
            except IOBudgetError as exc:
                result = {"ok": False, "reason": str(exc), "report": exc.report}
            except Exception as exc:
                result = {"ok": False, "reason": "validation_local_error", "report": {"error_type":type(exc).__name__,"detail":str(exc)[:600]}}
            finally:
                signal.setitimer(signal.ITIMER_REAL,0)
            raw = (json.dumps({"type":"result",**result})+'\n').encode()
            offset=0
            while offset<len(raw):offset+=os.write(write_fd,raw[offset:])
        finally:
            os._exit(0)
    os.close(write_fd)
    os.set_blocking(read_fd, False)
    reader=io.FileIO(read_fd,'rb',closefd=True);selector=selectors.DefaultSelector();selector.register(reader,selectors.EVENT_READ)
    buffer=b'';count=0;response=None;group=False;last={"phase":"worker_start"};reason=None;reaped=False;cleanup_error=None
    try:
        while time.monotonic()-started<seconds and response is None:
            for _,_ in selector.select(min(.05,max(0,seconds-(time.monotonic()-started)))):
                chunk=reader.read(65536)
                if chunk is None:continue
                if not chunk:
                    reason='validation_worker_closed_without_result';break
                count+=len(chunk)
                if count>LIMIT:
                    reason='validation_worker_output_limit';break
                buffer+=chunk
                while b'\n' in buffer:
                    line,buffer=buffer.split(b'\n',1);event=json.loads(line)
                    require(isinstance(event,dict),'invalid validation worker response')
                    if event.get('type')=='started':
                        require(event.get('pid')==pid and event.get('group')==pid,'validation worker group identity mismatch');group=True
                    elif event.get('type')=='progress':last={k:v for k,v in event.items() if k!='type'}
                    elif event.get('type')=='result':response=event
            if reason:break
        if response is None and reason is None:reason='validation_io_timeout'
    except (OSError,ValueError,TaskError) as exc:
        reason='validation_worker_protocol_error';last={"phase":"worker_read","error_type":type(exc).__name__}
    finally:
        denied_cleanup=last.get('cleanup_error')
        if denied_cleanup:cleanup_error=denied_cleanup
        uncertain_command = operation=='command' and response is not None and not response.get('ok') and response.get('report',{}).get('validator_possible_effects') and not denied_cleanup
        if response is not None and not reason and not uncertain_command:
            end=time.monotonic()+.1
            while time.monotonic()<end:
                try:
                    found,_=os.waitpid(pid,os.WNOHANG)
                    if found:reaped=True;break
                except ChildProcessError:reaped=True;break
                time.sleep(.002)
        # Never signal a numeric group after reaping its leader and allowing PID reuse.
        if not reaped and not denied_cleanup:
            try:
                if group:os.killpg(pid,signal.SIGKILL)
                else:os.kill(pid,signal.SIGKILL)
            except ProcessLookupError:pass
            except OSError as exc:cleanup_error=type(exc).__name__
        selector.close();reader.close()
        end=time.monotonic()+.25
        while not reaped and time.monotonic()<end:
            try:
                found,_=os.waitpid(pid,os.WNOHANG)
                if found:reaped=True;break
            except ChildProcessError:reaped=True;break
            time.sleep(.005)
    receipt={"operation":operation,"worker_pid":pid,"owned_group":pid if group else None,"worker_reaped":reaped,
             "elapsed_seconds":time.monotonic()-started,"limit_seconds":seconds,"last_observation":last,"cleanup_error":cleanup_error}
    if reason or not reaped or cleanup_error:
        original=response.get('reason') if response and not response.get('ok') else None
        raise IOBudgetError(reason or original or 'validation_worker_quiescence_unknown',
                            {**receipt,**(response.get('report',{}) if original else {}),"error_type":"IOBudgetError"})
    if not response.get('ok'):
        raise IOBudgetError(response.get('reason','validation_worker_failed'),{**receipt,**response.get('report',{})})
    return response['value'],receipt


def file_scope(spec, step):
    """Known destination paths and existing object aliases, without file reads.

    A workspace is an anchor, not file identity. Keep path identity as well as
    device/inode: replacing a denied destination cannot grant that destination,
    and a hard link/rename cannot grant access to the same existing object.
    Symlink and escaping-path support remains the existing safe_path contract.
    """
    if not _IO_CHILD:
        key=digest(encoded({'workspace':spec['workspace'],'checks':step['checks'],'resources':step.get('resources',[])}))
        if _IO_DEPTH and key in _SCOPE_FAILURES:return copy.deepcopy(_SCOPE_FAILURES[key])
        try:
            return isolated_io('scope',lambda:file_scope(spec,step),SCOPE_SECONDS)[0]
        except (TaskError,OSError) as exc:
            failed={"files":sorted({os.path.abspath(Path(spec['workspace'])/c['path']) for c in step['checks'] if c['kind']=='file'} | {os.path.abspath(Path(spec['workspace'])/r['identity']) for r in step.get('resources',[]) if r['kind']=='file'}),
                    "file_objects":[],"targets":[],"workspace_identity":None,
                    "file_identity_errors":[{"path":spec['workspace'],"error_type":type(exc).__name__,"io_budget":True,
                                             "io":getattr(exc,'report',{}),"detail":str(exc)}]}
            if _IO_DEPTH:_SCOPE_FAILURES[key]=copy.deepcopy(failed)
            return failed
    paths, objects, errors, targets = set(), set(), [], []
    workspace_identity = None
    try:
        io_progress('scope_workspace')
        workspace = cf.safe_path(spec["workspace"], ".")
        info = os.stat(workspace, follow_symlinks=False)
        require(stat.S_ISDIR(info.st_mode), "workspace is not a directory")
        workspace_identity = {"path": str(workspace.resolve()), "device": info.st_dev, "inode": info.st_ino}
    except IOBudgetError:
        raise
    except (OSError, cf.SyncError) as exc:
        errors.append({"path": spec["workspace"], "error_type": type(exc).__name__, "workspace": True})
    checked_paths={c['path'] for c in step['checks'] if c['kind']=='file'}
    file_checks=step['checks']+[{'kind':'file','path':r['identity']} for r in step.get('resources',[]) if r['kind']=='file' and r['identity'] not in checked_paths]
    for check in file_checks:
        if check["kind"] != "file":
            continue
        path = Path(os.path.abspath(Path(spec["workspace"]) / check["path"]))
        target = {"path": str(path), "exists": None}
        try:
            io_progress('scope_target',path=str(path))
            path = cf.safe_path(spec["workspace"], check["path"]).resolve()
            target["path"] = str(path)
            info = os.stat(path, follow_symlinks=False)
            target.update(exists=True, device=info.st_dev, inode=info.st_ino, size=info.st_size,
                          mtime_ns=info.st_mtime_ns, ctime_ns=info.st_ctime_ns, kind=stat.S_IFMT(info.st_mode))
            if info.st_ino > 0:
                objects.add((info.st_dev, info.st_ino))
        except FileNotFoundError:
            target["exists"] = False
        except IOBudgetError:
            raise
        except (OSError, cf.SyncError) as exc:
            errors.append({"path": str(path), "error_type": type(exc).__name__})
        paths.add(str(path)); targets.append(target)
    return {"files": sorted(paths), "file_objects": [{"device": d, "inode": i} for d, i in sorted(objects)],
            "file_identity_errors": errors, "targets": sorted(targets, key=lambda t: t["path"]),
            "workspace_identity": workspace_identity}


def restriction(spec, state, step, input_revision, execution):
    observed_scope = file_scope(spec, step)
    return {"workspace": str(Path(spec["workspace"]).resolve()), "step": step["id"], "instruction": step["instruction"],
            **observed_scope, "input_revision": input_revision, "wait": copy.deepcopy(state.get("wait") or {}),
            "state": "blocked" if state["state"] == "blocked" else "reconcile",
            "resource_effects": coordination.resources({"spec": spec}, step, observed_scope),
            "execution": state.get("unresolved_execution") or execution}


def remember_restriction(value, key):
    value["steps"][key].pop("constraint_derived", None)
    step = next(s for s in value["spec"]["steps"] if s["id"] == key)
    execution = value.get("execution")
    hold = restriction(value["spec"], value["steps"][key], step, value["input_revision"],
                       execution if execution and execution["step"] == key else None)
    holds = {digest(encoded(h)): h for h in value.get("restrictions", [])}
    holds[digest(encoded(hold))] = hold
    value["restrictions"] = list(holds.values())


def same_restricted_scope(hold, step, scope):
    if hold["files"] and scope["files"]:
        if set(hold["files"]).intersection(scope["files"]):
            return True
        old_objects = {(o["device"], o["inode"]) for o in hold.get("file_objects", [])}
        new_objects = {(o["device"], o["inode"]) for o in scope["file_objects"]}
        return bool(old_objects.intersection(new_objects))
    # A generic instruction cannot prove that two declared branches are the
    # same action. Without comparable file objects, retain the stable step ID;
    # arbitrary tool/resource semantics remain the registration owner's duty.
    return hold["step"] == step["id"]


def assessment(value, key, expected_scope=None, related=True, dependencies=True):
    spec = next(s for s in value["spec"]["steps"] if s["id"] == key)
    scope = file_scope(value["spec"], spec)
    matches = [h for h in value.get("restrictions", []) if same_restricted_scope(h, spec, scope)]
    reason = None
    if matches:
        reason = "current_object_restriction"
    elif spec.get("stable_objects_required"):
        reason = "stable_object_isolation_adapter_unavailable"
    elif any(e.get("workspace") or e.get("io_budget") for e in scope["file_identity_errors"]) or (value.get("restrictions") and scope["file_identity_errors"]):
        reason = "object_identity_not_established"
    elif expected_scope is not None and digest(encoded(scope)) != digest(encoded(expected_scope)):
        reason = "object_changed_between_decisions"
    elif value.get("accepted") and isinstance(value.get("acceptance_observation"), dict) and value["acceptance_observation"].get("scope_sha256", {}).get(key) != digest(encoded(scope)):
        reason = "accepted_object_changed; inspect current artifact before reacceptance"
    relationships = coordination.related(value, spec, dependencies=dependencies) if related and reason is None else []
    if reason is None and relationships:
        reason = "related_" + relationships[0]["kind"]
    return {"scope": scope, "reason": reason, "restriction_refs": [digest(encoded(h)) for h in matches],
            "source_waits": [h["wait"] for h in matches], "blocked": reason is not None,
            "related_conditions": relationships}


def enforce_constraints(value, phase, key=None, expected_scope=None):
    observations = {}
    for spec in value["spec"]["steps"]:
        name = spec["id"]
        if key is not None and name != key:
            continue
        observed = assessment(value, name, expected_scope if name == key else None)
        observations[name] = observed
        step = value["steps"][name]
        if not observed["blocked"] and step.get("coordination_wait") and step["state"] == "blocked":
            previous = step.pop("coordination_wait")
            if previous["state"] == "pending":
                step.update(state="pending", wait=previous["wait"])
                step.pop("constraint_derived", None)
        if observed["blocked"]:
            if observed["reason"].startswith("related_") and step["state"] == "pending" and "coordination_wait" not in step:
                step["coordination_wait"] = {"state": "pending", "wait": copy.deepcopy(step.get("wait"))}
            prior_wait = step.get("wait") or {}
            source = next((w.get("source_ref") or w.get("evidence_ref") for w in observed["source_waits"] if w.get("source_ref") or w.get("evidence_ref")), None)
            step.update(state="reconcile" if step["state"] == "reconcile" else "blocked", constraint_derived=True,
                        wait={**prior_wait, "kind": "object_constraint", "reason": observed["reason"],
                        "phase": phase, "source_ref": source or prior_wait.get("source_ref"), "restriction_refs": observed["restriction_refs"],
                        "related_conditions": observed["related_conditions"],
                        "next": "inspect_original_constraint_and_actual_object; use verified isolation if stronger stability is required"})
            value["accepted"] = False
        execution = value.get("execution")
        if execution and execution["step"] == name:
            execution.setdefault("constraint_checks", {})[phase] = {"blocked": observed["blocked"], "reason": observed["reason"],
                "scope_sha256": digest(encoded(observed["scope"])), "restriction_refs": observed["restriction_refs"]}
    return observations


def specification(value, filesystem=True):
    require(isinstance(value, dict), "task specification must be an object")
    require(set(value) <= {"request_id", "requirement", "acceptance_version", "workspace", "steps", "max_runs", "review_required", "load_required", "native_model", "native_reasoning_effort", "project", "assignment", "request_identity", "known_tasks", "requires", "artifact", "execution_mode", "result_scope"}, "unknown task specification field")
    if value.get("native_model") is not None:
        require(isinstance(value["native_model"], str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}", value["native_model"]), "invalid explicit native model")
    if value.get("native_reasoning_effort") is not None:
        require(value["native_reasoning_effort"] in ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"), "invalid explicit reasoning effort")
    for key in ("request_id", "requirement", "acceptance_version"):
        require(text(value.get(key)), "missing " + key)
    workspace = Path(value.get("workspace", ""))
    require(workspace.is_absolute(), "workspace must be an existing absolute directory")
    if filesystem:
        require(workspace.is_dir(), "workspace must be an existing absolute directory")
        cf.safe_path(workspace, ".")
    steps = value.get("steps")
    require(isinstance(steps, list) and 0 < len(steps) <= 32, "task needs 1..32 steps")
    seen = set()
    for step in steps:
        require(isinstance(step, dict) and set(step) <= {"id", "instruction", "depends_on", "checks", "approval_required", "stable_objects_required", "depends_on_basis", "resources"}, "invalid step fields")
        key = step.get("id")
        require(isinstance(key, str) and ID.fullmatch(key) and key not in seen, "invalid/duplicate step id")
        require(text(step.get("instruction")), "missing step instruction")
        dependencies = step.get("depends_on", [])
        require(isinstance(dependencies, list) and all(isinstance(d, str) and d in seen for d in dependencies) and len(set(dependencies)) == len(dependencies), "dependencies must name earlier steps")
        checks = step.get("checks")
        require(isinstance(checks, list) and 0 < len(checks) <= 16, "step needs explicit acceptance checks")
        for check in checks:
            require(isinstance(check, dict), "check must be an object")
            kind = check.get("kind")
            if kind == "file":
                require({"kind","path","sha256"} <= set(check) <= {"kind","path","sha256","timeout","max_bytes"} and isinstance(check["path"], str), "invalid file check")
                require(type(check.get('timeout',FILE_SECONDS)) is int and 1<=check.get('timeout',FILE_SECONDS)<=60,'invalid file timeout')
                require(type(check.get('max_bytes',FILE_BYTES)) is int and 1<=check.get('max_bytes',FILE_BYTES)<=2**40,'invalid file byte budget')
                require(not Path(check["path"]).is_absolute() and ".." not in Path(check["path"]).parts, "invalid relative file check path")
                if filesystem:
                    cf.safe_path(workspace, check["path"])
                require(isinstance(check["sha256"], str) and SHA.fullmatch(check["sha256"]), "invalid file hash")
            elif kind == "message":
                require(set(check) == {"kind", "equals"} and text(check["equals"]), "invalid message check")
            elif kind == "command":
                require({"kind","argv","timeout"} <= set(check) <= {"kind","argv","timeout","max_output_bytes"}, "invalid command check fields")
                require(isinstance(check["argv"], list) and 0 < len(check["argv"]) <= 32 and all(text(a) for a in check["argv"]), "invalid check argv")
                require(type(check["timeout"]) is int and 1 <= check["timeout"] <= 60, "invalid check timeout")
                require(type(check.get('max_output_bytes',COMMAND_OUTPUT_BYTES)) is int and 1<=check.get('max_output_bytes',COMMAND_OUTPUT_BYTES)<=2**30,'invalid validator output budget')
            else:
                raise TaskError("unsupported check kind")
        require(type(step.get("approval_required", False)) is bool, "approval_required must be boolean")
        require(type(step.get("stable_objects_required", False)) is bool, "stable_objects_required must be boolean")
        seen.add(key)
    require(type(value.get("max_runs", 3)) is int and 1 <= value.get("max_runs", 3) <= 8, "max_runs must be 1..8")
    for key in ("review_required", "load_required"):
        require(type(value.get(key, False)) is bool, key + " must be boolean")
    coordination.validate(value)
    return copy.deepcopy(value)


NO_CHANGE = object()


class Store:
    def __init__(self, codex_home, task_id):
        require(isinstance(task_id, str) and ID.fullmatch(task_id), "invalid task id")
        self.root = cf.safe_path(codex_home, "task-runtime")
        self.path = cf.safe_path(self.root, task_id + ".json")
        self.lock = cf.safe_path(self.root, task_id + ".lock")

    @contextmanager
    def locked(self):
        self.root.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.lock, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)

    def read(self):
        raw = cf.read_file(self.path)
        require(raw is not None and len(raw) <= LIMIT, "task missing or record too large")
        value = json.loads(raw)
        require(isinstance(value, dict) and value.get("format") == 1 and type(value.get("revision")) is int, "invalid task state")
        require(value.get("record_sha256") == digest(encoded({k: v for k, v in value.items() if k != "record_sha256"})), "task record fingerprint mismatch")
        fields = {"format", "task_id", "revision", "input_revision", "spec", "acceptance_sha256", "steps", "execution", "native_session_id", "history", "review", "loaded", "delivery", "accepted", "record_sha256"}
        require(fields <= set(value) <= fields | {"restrictions", "acceptance_observation", "result_state"} and isinstance(value.get("restrictions", []), list), "invalid task record fields")
        require(value["revision"] >= 1 and type(value["input_revision"]) is int and value["input_revision"] >= 1 and type(value["accepted"]) is bool, "invalid task version or acceptance")
        spec = specification(value["spec"], filesystem=False)
        require(value["acceptance_sha256"] == digest(encoded(spec)), "acceptance specification mismatch")
        require(isinstance(value["steps"], dict) and set(value["steps"]) == {s["id"] for s in spec["steps"]}, "task step set mismatch")
        for step in value["steps"].values():
            require(isinstance(step, dict) and step.get("state") in ("pending", "running", "verified", "blocked", "reconcile") and type(step.get("runs")) is int and step["runs"] >= 0 and isinstance(step.get("evidence"), list), "invalid step state")
        require(value["execution"] is None or isinstance(value["execution"], dict), "invalid execution state")
        require(isinstance(value["history"], list) and len(value["history"]) <= 64 and isinstance(value["delivery"], dict), "invalid task history/delivery")
        coordination.validate_result_state(value)
        return value

    def save(self, value):
        value.pop("record_sha256", None)
        value["record_sha256"] = digest(encoded(value))
        raw = encoded(value)
        require(len(raw) <= LIMIT, "task record size limit; preserve evidence references rather than raw history")
        cf.write_file(self.path, raw)

    def create(self, task_id, spec):
        spec = specification(spec)
        with self.locked():
            require(not self.path.exists(), "task exists; use its revision and an explicit amendment")
            value = {"format": 1, "task_id": task_id, "revision": 1, "input_revision": 1,
                     "spec": spec, "acceptance_sha256": digest(encoded(spec)), "steps": {},
                     "execution": None, "native_session_id": None, "history": [], "restrictions": [], "review": None, "loaded": None,
                     "delivery": {"sent": False, "read": False, "user_decision": None}, "accepted": False}
            value["steps"] = {step["id"]: {"state": "pending", "runs": 0, "session_id": None,
                                           "evidence": [], "validation": None, "wait": None} for step in spec["steps"]}
            coordination.membership(value)
            coordination.initialize_result(value)
            self.save(value)
            return value

    @coordination.scoped
    def update(self, expected, operation, reservation=False):
        require(type(expected) is int, "expected_revision is required")
        with self.locked():
            value = self.read()
            if value["revision"] != expected:
                raise RevisionConflict("task revision conflict; reread before changing it")
            def apply():
                if operation(value) is NO_CHANGE:
                    return value
                value["revision"] += 1
                # Publish a conservative claim before the task record: a crash
                # cannot leave a live producer invisible to another task.
                if reservation:
                    coordination.sync_index(self, value)
                self.save(value)
                if not reservation:
                    coordination.sync_index(self, value)
                return value
            if reservation:
                with coordination.index_lock(self.root):
                    return apply()
            return apply()


def partition(value, observations=None, selected=None):
    ready, waiting, done, unresolved = [], [], [], []
    specs = [s for s in value["spec"]["steps"] if selected is None or s["id"] == selected]
    observations = observations or {s["id"]: assessment(value, s["id"]) for s in specs}
    for spec in specs:
        key = spec["id"]
        step = value["steps"][key]
        if observations[key]["blocked"]:
            row={"step": key, "condition": observations[key]["reason"], "restriction_refs": observations[key]["restriction_refs"]}
            conditions=observations[key]['related_conditions']
            if (any(c['kind']=='local_dependency_evidence_required' for c in conditions)
                    and not any(c['kind'] in ('retained_refusal','occupied_effect') for c in conditions)):
                row['evidence_gaps']=observations[key]['related_conditions'];unresolved.append(row)
            else:waiting.append(row)
        elif step.get("continuation") and not step["continuation"]["resolved"]:
            waiting.append({"step": key, "condition": "original_continuation_requires_readback", "origin": step["continuation"]["origin"]})
        elif step["state"] == "verified":
            done.append(key)
        elif step["state"] in ("blocked", "reconcile", "running") and not (
                step["state"] == "blocked" and step.get("coordination_wait", {}).get("state") == "pending"):
            waiting.append({"step": key, "condition": step.get("wait") or step["state"]})
        elif any(value["steps"][d]["state"] != "verified" for d in coordination.local_dependencies(spec)):
            waiting.append({"step": key, "condition": "dependencies", "depends_on": coordination.local_dependencies(spec)})
        elif spec.get("approval_required") and step.get("approval", {}).get("decision") != "approved":
            waiting.append({"step": key, "condition": "explicit_approval_decision_required"})
        else:
            ready.append(key)
    return {"ready": ready, "waiting": waiting, "verified": done, "unresolved": unresolved}


@io_call
def view(value):
    result = copy.deepcopy(value)
    result["execution_guarantee"] = "coordinator boundary checks; no OS object isolation or global native-tool interception"
    observations = {s["id"]: assessment(value, s["id"]) for s in value["spec"]["steps"]}
    result["current_constraints"] = observations
    if any(o["blocked"] for o in observations.values()):
        result["acceptance_receipt_before_current_observation"] = value["accepted"]
        result["accepted"] = False
    result["work_partition"] = partition(value, observations)
    if coordination.result_state(value) is not None:
        result["result_dimensions"], result_error = coordination.result_observation(value)
        if result["accepted"] and result_error is not None:
            result["accepted"]=False
            result["result_dimensions"]["current_result_error"]=result_error
            result["result_dimensions"]["business_outcome"]="unverified"
    result["completion_scope"] = "this task only; not project or all-project completion"
    if value["spec"].get("project"):
        result["project_context"] = copy.deepcopy(value["spec"]["project"])
        result["assignment"] = copy.deepcopy(value["spec"]["assignment"])
        result["current_request"] = {"request_id": value["spec"]["request_id"], "task_id": value["task_id"], "input_revision": value["input_revision"], "task_input_sha256": value["acceptance_sha256"], "request_identity": value["spec"].get("request_identity")}
    result["unproven_dependencies"] = [e for e in value["spec"].get("requires", []) if e["basis"]["status"] != "proven"]
    if result["work_partition"]["ready"]:
        result["next_action"] = {"kind": "execute_or_collect", "step": result["work_partition"]["ready"][0], "bounded": True}
    elif result.get("result_dimensions",{}).get("reviewable_results"):
        result["next_action"] = {"kind":"review_original_results","results":result["result_dimensions"]["reviewable_results"],"bounded":True}
    elif result.get("result_dimensions",{}).get("continuable_results"):
        result["next_action"] = {**result["result_dimensions"]["continuable_results"][0],"bounded":True}
    elif result.get("result_dimensions",{}).get("pending_obligations") and not result["work_partition"]["waiting"]:
        result["next_action"] = {"kind":"continue_original_obligations","obligations":result["result_dimensions"]["pending_obligations"],"bounded":True}
    elif len(result["work_partition"]["verified"]) == len(value["steps"]):
        result["next_action"] = {"kind": "completed" if result["accepted"] else "review_load_or_accept", "bounded": True}
    else:
        result["next_action"] = {"kind": "resolve_specific_wait", "conditions": result["work_partition"]["waiting"], "bounded": True}
    return result


def checked_file(value, step_id, check):
    """Hash the opened ordinary file, checking its actual FD before any bytes.

    This binds the controller's read, not arbitrary native tool operations.
    External writers can still change an object after this observation.
    """
    if not _IO_CHILD:
        result,receipt=isolated_io('file',lambda:checked_file(value,step_id,check),check.get('timeout',FILE_SECONDS))
        return {**result,'io':receipt}
    started=time.monotonic();byte_limit=check.get('max_bytes',FILE_BYTES);total=0
    passive = step_id is None
    step = ({"id": None, "checks": [check], "resources": []} if passive else
            next(s for s in value["spec"]["steps"] if s["id"] == step_id))
    # Dependency closure belongs to the outer validation/acceptance decision.
    # Re-entering it inside every FD read recursively scans loaded artifacts
    # and floods this bounded worker's progress channel. Keep actual object
    # restrictions and peer effects checked on both sides of the read.
    if passive:
        # No borrowed step: this read is checked against its actual object.
        # Preserve bounded workspace/path observation before opening its FD.
        scope=file_scope(value["spec"],step)
        require(not scope["file_identity_errors"], "passive evidence object identity unavailable")
        before={"scope":scope}
    else:
        before = assessment(value, step_id, dependencies=False)
        require(not before["blocked"], before["reason"] or "object constraint")
    path = cf.safe_path(value["spec"]["workspace"], check["path"])
    parts = Path(check["path"]).parts
    require(parts, "file check must name an ordinary file")
    directory = None; descriptor = None
    try:
        io_progress('open_workspace')
        flags=os.O_RDONLY | os.O_NONBLOCK | getattr(os,'O_NOFOLLOW',0)
        directory = os.open(value["spec"]["workspace"], flags | os.O_DIRECTORY)
        root_info = os.fstat(directory)
        root_identity = before["scope"]["workspace_identity"]
        require(root_identity and (root_info.st_dev, root_info.st_ino) == (root_identity["device"], root_identity["inode"]), "workspace identity changed before file open")
        for part in parts[:-1]:
            io_progress('open_parent')
            next_directory = os.open(part, flags | os.O_DIRECTORY, dir_fd=directory)
            os.close(directory);directory = next_directory
        try:
            io_progress('open_target')
            descriptor = os.open(parts[-1], flags, dir_fd=directory)
        except FileNotFoundError:
            return {"actual_sha256": None, "passed": False}
        io_progress('fstat_target')
        start = os.fstat(descriptor)
        require(stat.S_ISREG(start.st_mode), "expected ordinary file: " + str(path))
        require(start.st_size<=byte_limit,'validation file exceeds declared byte budget')
        actual_scope = {"files": [str(path.resolve())], "file_objects": [{"device": start.st_dev, "inode": start.st_ino}]}
        require(not any(same_restricted_scope(h, step, actual_scope) for h in value.get("restrictions", [])), "opened descriptor matches retained object restriction")
        coordination.check_passive_object(value,str(path.resolve()),start.st_dev,start.st_ino,step_id)
        hasher = hashlib.sha256()
        io_progress('read_target')
        while True:
            require(time.monotonic()-started<check.get('timeout',FILE_SECONDS),'validation file read deadline exceeded')
            chunk = os.read(descriptor, 1048576)
            if not chunk:
                break
            total+=len(chunk)
            require(total<=byte_limit,'validation file grew beyond declared byte budget')
            hasher.update(chunk)
        io_progress('post_read_identity')
        end = os.fstat(descriptor)
        require((start.st_dev,start.st_ino,start.st_size,start.st_mtime_ns,start.st_ctime_ns) ==
                (end.st_dev,end.st_ino,end.st_size,end.st_mtime_ns,end.st_ctime_ns), "file changed during validation")
        current = os.stat(cf.safe_path(value["spec"]["workspace"], check["path"]), follow_symlinks=False)
        require((current.st_dev,current.st_ino,current.st_size,current.st_mtime_ns,current.st_ctime_ns) ==
                (end.st_dev,end.st_ino,end.st_size,end.st_mtime_ns,end.st_ctime_ns), "file target changed after validation read")
        coordination.check_passive_object(value,str(path.resolve()),end.st_dev,end.st_ino,step_id)
        if not passive:
            after = assessment(value, step_id, dependencies=False)
            require(not after["blocked"], after["reason"] or "object constraint after read")
        actual = hasher.hexdigest()
        return {"actual_sha256": actual, "passed": actual == check["sha256"], "opened_object": actual_scope["file_objects"][0]}
    except FileNotFoundError:
        require(descriptor is None, "file target disappeared after opening; inspect without replay")
        return {"actual_sha256": None, "passed": False}
    finally:
        if descriptor is not None:os.close(descriptor)
        if directory is not None:os.close(directory)


def checked_command(workspace, check):
    if not _IO_CHILD:
        result,receipt=isolated_io('command',lambda:checked_command(workspace,check),check['timeout']+.5)
        return {**result,'io':receipt}
    process=None;selector=selectors.DefaultSelector();hashes={'out':hashlib.sha256(),'err':hashlib.sha256()};counts={'out':0,'err':0}
    started=time.monotonic();reason=None
    try:
        io_progress('command_spawn')
        process=subprocess.Popen(check['argv'],cwd=workspace,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0)
        io_progress('command_wait',command_pid=process.pid)
        for stream,key in [(process.stdout,'out'),(process.stderr,'err')]:
            os.set_blocking(stream.fileno(),False);selector.register(stream,selectors.EVENT_READ,key)
        while selector.get_map() or process.poll() is None:
            if time.monotonic()-started>=check['timeout']:
                reason='validation_timeout_effect_unknown';break
            for key,_ in selector.select(.02):
                chunk=key.fileobj.read(65536)
                if chunk is None:continue
                if not chunk:selector.unregister(key.fileobj);continue
                counts[key.data]+=len(chunk);hashes[key.data].update(chunk)
                if sum(counts.values())>check.get('max_output_bytes',COMMAND_OUTPUT_BYTES):reason='validation_output_limit_effect_unknown';break
            if reason:break
        facts={'stdout_sha256':hashes['out'].hexdigest(),'stderr_sha256':hashes['err'].hexdigest(),
               'stdout_bytes':counts['out'],'stderr_bytes':counts['err'],'command_pid':process.pid}
        if reason:
            raise IOBudgetError(reason,{**facts,'error_type':'IOBudgetError','validator_possible_effects':True})
        return {**facts,'exit':process.returncode,'passed':process.returncode==0}
    finally:
        selector.close()
        if process:
            if process.poll() is None:
                try:process.kill()
                except ProcessLookupError:pass
                except OSError as exc:io_progress('command_cleanup_denied',command_pid=process.pid,cleanup_error=type(exc).__name__)
                try:process.wait(timeout=.05)
                except subprocess.TimeoutExpired:pass
            process.stdout.close();process.stderr.close()


def validate(value, step_id, message):
    """Mandatory at the real result consumer, never inferred from a worker final."""
    spec = next(s for s in value["spec"]["steps"] if s["id"] == step_id)
    workspace = Path(value["spec"]["workspace"])
    results = {}
    # Run repeatable command validators before passive file reads. A command
    # result cannot make an earlier file observation current by itself.
    ordered = sorted(enumerate(spec["checks"]), key=lambda item: item[1]["kind"] == "file")
    for index, check in ordered:
        kind = check["kind"]
        row = {"kind": kind, "passed": False}
        try:
            current = assessment(value, step_id)
            require(not current["blocked"], current["reason"] or "current object constraint")
            if kind == "file":
                row["path"] = check["path"]
                row.update(checked_file(value, step_id, check))
            elif kind == "message":
                row["actual_sha256"] = digest(message.encode())
                row["passed"] = message == check["equals"]
            else:
                row.update(checked_command(workspace,check))
        except IOBudgetError as exc:
            spawn_failed = str(exc)=='validation_local_error' and exc.report.get('last_observation',{}).get('phase')=='command_spawn' and exc.report.get('error_type') in ('OSError','FileNotFoundError','PermissionError','NotADirectoryError')
            row.update(error=str(exc),error_type=exc.report.get('error_type','IOBudgetError'),detail=exc.report.get('detail',str(exc)),
                       io=exc.report,validator_possible_effects=kind=='command' and exc.report.get('validator_possible_effects',not spawn_failed))
        except subprocess.TimeoutExpired:
            row["error"] = "validation_timeout_effect_unknown"
        except (OSError, cf.SyncError, ValueError) as exc:
            row.update(error="validation_local_error", error_type=type(exc).__name__, detail=str(exc)[:600])
        results[index] = row
        if row.get('error') or assessment(value, step_id)["blocked"]:
            break
    rows = [results[i] for i in sorted(results)]
    final_constraints = assessment(value, step_id)
    return {"execution_id": value["execution"]["id"], "input_revision": value["input_revision"],
            "acceptance_sha256": value["acceptance_sha256"], "checks": rows,
            "passed": len(rows) == len(spec["checks"]) and all(row["passed"] for row in rows) and not final_constraints["blocked"],
            "scope_after": file_scope(value["spec"], spec), "validator": "task_runtime.validate/v1"}


@coordination.scoped
@io_call
def reserve(store, expected):
    selected = {}
    def begin(value):
        require(not value["accepted"], "task already accepted")
        require(value["execution"] is None, "an execution is unresolved; reconcile it before dispatch")
        observed=enforce_constraints(value, "reserve")
        available = partition(value,observed)["ready"]
        if not available:
            return
        key = available[0]
        step = value["steps"][key]
        require(step["runs"] < value["spec"].get("max_runs", 3), "bounded run limit reached")
        execution = {"id": uuid.uuid4().hex, "controller_pid": os.getpid(), "input_revision": value["input_revision"],
                     "acceptance_sha256": value["acceptance_sha256"], "step": key,
                     "session_id": step["session_id"] or value["native_session_id"], "worker_pid": None,
                     "state": "dispatching", "started_at": time.time()}
        execution["execution_guarantee"] = "coordinator_only"
        execution["dispatch_scope"] = file_scope(value["spec"], next(s for s in value["spec"]["steps"] if s["id"] == key))
        import framework_runtime as runtime
        context = runtime.executing()
        execution["controller"] = {"revision": context.get("revision"), "directory": context.get("directory"),
                                   "module_sha256": digest(Path(__file__).read_bytes())}
        value["execution"] = execution
        step.update(state="running", runs=step["runs"] + 1)
        value["review"] = value["loaded"] = None
        selected.update(execution)
    value = store.update(expected, begin, reservation=True)
    return value, selected


def execution_update(store, expected, execution_id, operation):
    try:
        return store.update(expected, operation)
    except RevisionConflict:
        current = store.read()
        execution = current.get("execution")
        require(execution and execution["id"] == execution_id and execution["input_revision"] == current["input_revision"] and execution["acceptance_sha256"] == current["acceptance_sha256"], "stale execution result; current task not changed")
        return store.update(current["revision"], operation)


@coordination.scoped
@io_call
def finish(store, expected, execution_id, worker):
    require(isinstance(worker, dict) and worker.get("status") in ("completed", "failed", "unknown"), "invalid worker outcome")
    observation = {k: worker.get(k) for k in ("status", "session_id", "native_turn_id", "native_turn_sha256", "native_error", "tool_failures", "pid", "started_at", "ended_at", "exit", "usage", "events_sha256", "message_sha256", "error", "prompt_sent")}
    observation["execution_id"] = execution_id
    def identify(value):
        execution = value.get("execution")
        require(execution and execution["id"] == execution_id, "stale execution result; current task not changed")
        require(execution["input_revision"] == value["input_revision"] and execution["acceptance_sha256"] == value["acceptance_sha256"], "stale acceptance version")
        return execution
    def observed(value):
        execution = identify(value)
        step = value["steps"][execution["step"]]
        require(worker.get("session_id") is None or isinstance(worker["session_id"], str), "invalid native session identity")
        if worker.get("session_id"):
            require(execution.get("session_id") in (None, worker["session_id"]), "native result belongs to a different original session")
            execution["session_id"] = worker["session_id"]
            step["session_id"] = worker["session_id"]
            value["native_session_id"] = worker["session_id"]
        step["evidence"].append(observation)
        execution["state"] = "validating" if worker["status"] == "completed" else "observed"
        if worker["status"] == "completed":execution["validation_started"] = True
        enforce_constraints(value, "native_result", execution["step"])
    snapshot = execution_update(store, expected, execution_id, observed)
    # Persist observation and validation intent first, release the task lock for
    # actual checks, then compare the same execution at the short commit point.
    checked = validate(snapshot, snapshot["execution"]["step"], worker.get("message", "")) if worker["status"] == "completed" else None
    if worker["status"] == "completed":
        require(isinstance(checked, dict) and checked.get("validator") == "task_runtime.validate/v1" and checked.get("execution_id") == execution_id and checked.get("acceptance_sha256") == snapshot["acceptance_sha256"], "mandatory result validator did not run")
    def consume(value):
        execution = identify(value)
        step = value["steps"][execution["step"]]
        constraints = enforce_constraints(value, "result_commit", execution["step"], checked.get("scope_after") if checked else None)[execution["step"]]
        # Invalid/failed/unknown transport is not a safe automatic retry.
        if worker.get("status") != "completed":
            declined = worker.get("error") == "native_permission_declined"
            coordinator_stop = worker.get("error") in ("coordinator_pre_prompt_blocked", "coordinator_scope_changed_after_prompt")
            if not coordinator_stop:
                step.update(state="reconcile" if worker.get("status") == "unknown" and not declined else "blocked",
                            wait={**(step.get('wait') or {}), "kind": "explicit_denial" if declined else worker["status"], "execution_id": execution_id,
                                  "next": "retain_original_native_denial" if declined else "inspect_native_result_and_actual_effect_once"})
            else:
                step.update(state="reconcile" if worker.get("prompt_sent") else "blocked", constraint_derived=True)
                step["wait"] = {**(step.get("wait") or {}), "kind": "object_constraint", "error": worker["error"],
                                "possible_effects": worker.get("prompt_sent") is not False, "next": "inspect_original_result_and_current_object; do not repeat"}
            if worker.get("prompt_sent") is not False:
                step["unresolved_execution"] = execution
            if worker.get("status") == "unknown":
                value["native_session_id"] = None
            if declined:
                remember_restriction(value, execution["step"])
            value["history"].append({"execution": execution, "worker_observation": observation})
            value["execution"] = None
            return
        step["validation"] = checked
        prior = step.get("last_validation_sha256")
        fingerprint = digest(encoded([{k:v for k,v in row.items() if k not in ('io','command_pid')} for row in checked['checks']]))
        step["last_validation_sha256"] = fingerprint
        if constraints["blocked"] or step.get("constraint_derived"):
            step.update(state="blocked", constraint_derived=True)
            step["wait"] = {**(step.get("wait") or {}), "possible_effects": worker.get("prompt_sent") is not False}
            step["unresolved_execution"] = execution
        elif checked["passed"]:
            step.update(state="verified", wait=None)
        elif any(row.get("error") for row in checked["checks"]):
            uncertain=any(row.get('validator_possible_effects') for row in checked['checks'])
            step.update(state="reconcile" if uncertain else "blocked", wait={"kind": "validation_error", "next": "diagnose_exact_validator_error_without_replay", "checks": checked["checks"],
                        'validator_possible_effects':uncertain})
            if uncertain:step['unresolved_execution']=execution
        elif worker.get("tool_failures"):
            step.update(state="blocked", wait={"kind": "tool_failure_requires_diagnosis", "next": "inspect_original_failure_before_retry", "checks": checked["checks"]})
        elif prior == fingerprint or step["runs"] >= value["spec"].get("max_runs", 3):
            step.update(state="blocked", wait={"kind": "no_progress", "next": "bounded_investigation_of_failed_checks", "checks": checked["checks"]})
        else:
            step.update(state="pending", wait=None)
        value["history"].append({"execution": execution, "validation": checked})
        require(len(value["history"]) <= 64, "task history bound reached")
        value["execution"] = None
    return execution_update(store, snapshot["revision"], execution_id, consume)


@coordination.scoped
def run_once(store, expected):
    from task_worker import execute
    value, execution = reserve(store, expected)
    if not execution:
        return value
    step = next(s for s in value["spec"]["steps"] if s["id"] == execution["step"])
    prompt = ("Framework task execution " + execution["id"] + "\nRequest: " + value["spec"]["request_id"] +
              "\nAcceptance version: " + value["acceptance_sha256"] + "\nGoal: " + value["spec"]["requirement"] +
              "\nCurrent step: " + step["instruction"] + "\nAttempt: " + str(value["steps"][execution["step"]]["runs"]) +
              "\nPrevious validation: " + json.dumps(value["steps"][execution["step"]].get("validation"), ensure_ascii=False) +
              "\nRequired checks: " + json.dumps(step["checks"], ensure_ascii=False) +
              "\nRetained scoped restrictions: " + json.dumps(value.get("restrictions", []), ensure_ascii=False) + coordination.prompt(value) +
              "\nContinue only this step. Inspect existing effects before repeating work. Keep native permissions and all refusals. A final message alone does not complete this task.")
    revision = value["revision"]
    def observed_process(pid):
        nonlocal revision
        allowed = {}
        def remember(current):
            require(type(pid) is int and pid > 0, "invalid native worker process identity")
            require(current["execution"] and current["execution"]["id"] == execution["id"], "execution superseded before native process observation")
            current["execution"]["worker_pid"] = pid
            observed = enforce_constraints(current, "before_prompt", execution["step"], current["execution"]["dispatch_scope"])[execution["step"]]
            allowed["value"] = not observed["blocked"]
            current["execution"]["prompt_authorized"] = allowed["value"]
        revision = execution_update(store, revision, execution["id"], remember)["revision"]
        return allowed["value"]
    def observed_session(identity):
        nonlocal revision
        allowed = {}
        def remember(current):
            require(current["execution"] and current["execution"]["id"] == execution["id"], "execution superseded before native session observation")
            current["execution"]["session_id"] = identity
            current["steps"][execution["step"]]["session_id"] = identity
            current["native_session_id"] = identity
            observed = enforce_constraints(current, "native_started", execution["step"])[execution["step"]]
            allowed["value"] = not observed["blocked"]
        revision = execution_update(store, revision, execution["id"], remember)["revision"]
        return allowed["value"]
    result = execute(prompt, value["spec"]["workspace"], execution["session_id"], on_session=observed_session,
                     on_process=observed_process,
                     model=value["spec"].get("native_model"), reasoning_effort=value["spec"].get("native_reasoning_effort"))
    try:
        return finish(store, revision, execution["id"], result)
    except TaskError:
        # Preserve the late observation without changing any current task state.
        observation = {"execution": execution, "worker": {k: v for k, v in result.items() if k != "message"},
                       "state": "unapplied_current_revision_conflict"}
        cf.write_file(cf.safe_path(store.root, execution["id"] + ".observation.json"), encoded(observation))
        raise


@coordination.scoped
@io_call
def run(store, expected, max_turns=3):
    require(type(max_turns) is int and 1 <= max_turns <= 4, "one invocation allows 1..4 native turns")
    value = store.read()
    require(value["spec"].get("execution_mode", "native") == "native", "external tasks collect existing effects without model dispatch")
    require(value["revision"] == expected, "task revision conflict")
    value = store.update(expected, lambda current: enforce_constraints(current, "run"))
    for _ in range(max_turns):
        if not partition(value)["ready"]:
            break
        value = run_once(store, value["revision"])
    if not value["accepted"] and all(s["state"] == "verified" for s in value["steps"].values()) and not value["spec"].get("review_required") and not value["spec"].get("load_required") and (coordination.result_state(value) is None or not coordination.result_state(value)["pending"]):
        value = change(store, {"operation": "accept", "expected_revision": value["revision"]})
    return value


def require_stopped(execution):
    """A passive result cannot finish ownership while a known producer is live."""
    for key in ("controller_pid", "worker_pid"):
        pid = execution.get(key)
        if key == "worker_pid" and pid is None:
            continue
        require(type(pid) is int and pid > 0, "original " + key + " identity missing; inspect before recovery")
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        except PermissionError as exc:
            raise TaskError("original " + key + " liveness unknown; do not dispatch a duplicate") from exc
        raise TaskError("original " + key + " is still running; retain its execution and do not dispatch a duplicate")


@coordination.scoped
@io_call
def resume(store, max_turns=3):
    """Restart from durable facts only; never invent a fresh request or effect."""
    value = store.read()
    require(value["spec"].get("execution_mode", "native") == "native", "external task outcome requires explicit effect collection/reconciliation, not model resume")
    execution = value.get("execution")
    if execution is None:
        pending = next((s.get("unresolved_execution") for s in value["steps"].values() if s["state"] == "reconcile" and
                        isinstance(s.get("unresolved_execution"), dict) and
                        s["unresolved_execution"].get("input_revision") == value["input_revision"] and
                        s["unresolved_execution"].get("acceptance_sha256") == value["acceptance_sha256"]), None)
        if pending:
            require_stopped(pending)
            def recover(current):
                require(current["execution"] is None, "another execution was reserved before native recovery")
                current["execution"] = copy.deepcopy(pending)
            value = store.update(value["revision"], recover)
            execution = value["execution"]
    if execution:
        require_stopped(execution)
        spec = next(s for s in value["spec"]["steps"] if s["id"] == execution["step"])
        if (execution.get("state") == "validating" or execution.get("validation_started")) and any(c["kind"] == "command" for c in spec["checks"]):
            value = finish(store, value["revision"], execution["id"], {"status": "unknown", "session_id": execution.get("session_id"),
                            "error": "validation_command_outcome_unknown_inspect_original_effect_no_replay"})
        elif execution.get("session_id"):
            from task_worker import inspect_session
            observation = inspect_session(execution["session_id"], execution["id"], value["spec"]["workspace"])
            value = finish(store, value["revision"], execution["id"], observation)
        elif all(c["kind"] == "file" for c in spec["checks"]):
            value = change(store, {"operation": "reconcile", "expected_revision": value["revision"],
                                  "step": execution["step"], "source_ref": "restart/passive-target-readback/" + execution["id"]})
        else:
            value = finish(store, value["revision"], execution["id"], {"status": "unknown", "session_id": None,
                                  "error": "missing_native_identity_inspect_original_effect_no_replay"})
    if partition(value)["ready"]:
        value = run(store, value["revision"], max_turns)
    elif not value["accepted"] and all(s["state"] == "verified" for s in value["steps"].values()) and not value["spec"].get("review_required") and not value["spec"].get("load_required") and (coordination.result_state(value) is None or not coordination.result_state(value)["pending"]):
        value = change(store, {"operation": "accept", "expected_revision": value["revision"]})
    return value


@coordination.scoped
def change(store, request):
    operation = request["operation"]
    def apply(value):
        if operation == "amend":
            spec = specification(request.get("spec"))
            prior_result = copy.deepcopy(coordination.result_state(value))
            retained = {k:copy.deepcopy(v["continuation"]) for k,v in value["steps"].items() if v.get("continuation")}
            require(set(retained) <= {s["id"] for s in spec["steps"]}, "amendment cannot drop original continuation steps")
            prior_spec = value["spec"]
            prior_workspace = value["spec"]["workspace"]
            prior_session = value["native_session_id"]
            old = {"input_revision": value["input_revision"], "acceptance_sha256": value["acceptance_sha256"], "spec": prior_spec,
                   "execution": value["execution"], "steps": value["steps"], "delivery": value["delivery"],
                   "acceptance_observation": value.get("acceptance_observation"), "result_state": prior_result}
            value["history"].append({"superseded_input": old})
            value["input_revision"] += 1
            value["spec"] = spec
            value["acceptance_sha256"] = digest(encoded(spec))
            value["review"] = value["loaded"] = None
            value["accepted"] = False
            value["acceptance_observation"] = None
            value["delivery"] = {"sent": False, "read": False, "user_decision": None}
            value["steps"] = {s["id"]: {"state": "pending", "runs": 0, "session_id": None,
                                         "evidence": [], "validation": None, "wait": None} for s in spec["steps"]}
            coordination.initialize_result(value,prior_result)
            for key,checkpoint in retained.items():value["steps"][key]["continuation"]=checkpoint
            # A new acceptance version changes criteria, not an existing refusal
            # or unresolved producer. Preserve the actual affected scope, also
            # when a file-effect step is renamed or its expected bytes change.
            holds = {digest(encoded(h)): h for h in value.get("restrictions", [])}
            for prior in prior_spec["steps"]:
                previous = old["steps"][prior["id"]]
                for hold in previous.get("holds", []):
                    holds[digest(encoded(hold))] = hold
                wait = previous.get("wait") or {}
                uncertain = previous["state"] in ("running", "reconcile") and (previous.get("unresolved_execution") or (old["execution"] and old["execution"]["step"] == prior["id"]))
                if uncertain or (not previous.get("constraint_derived") and wait.get("kind") in ("explicit_denial", "external_condition")):
                    hold = restriction(prior_spec, previous, prior, old["input_revision"],
                                       old["execution"] if old["execution"] and old["execution"]["step"] == prior["id"] else None)
                    if previous.get("constraint_derived") and uncertain:
                        hold["state"] = "reconcile"
                        hold["wait"] = {"kind": "unresolved_prior_execution", "execution_id": hold["execution"]["id"]}
                    if uncertain and hold["execution"].get("dispatch_scope"):
                        dispatched = hold["execution"]["dispatch_scope"]
                        hold["files"] = sorted(set(hold["files"]) | set(dispatched["files"]))
                        identities = {(o["device"],o["inode"]) for o in hold["file_objects"] + dispatched["file_objects"]}
                        hold["file_objects"] = [{"device":d,"inode":i} for d,i in sorted(identities)]
                    holds[digest(encoded(hold))] = hold
            value["restrictions"] = list(holds.values())
            for current in spec["steps"]:
                scope = file_scope(spec, current)
                matching = [h for h in holds.values() if same_restricted_scope(h, current, scope)]
                if matching:
                    step = value["steps"][current["id"]]
                    step["holds"] = matching
                    blocked = next((h for h in matching if h["state"] == "blocked"), None)
                    step.update(state="blocked" if blocked else "reconcile",
                                wait=copy.deepcopy(blocked["wait"] if blocked else {"kind": "superseded_effect_unknown", "original_steps": [h["step"] for h in matching]}))
                    held_execution = next((h["execution"] for h in matching if h["execution"]), None)
                    if held_execution:
                        step["unresolved_execution"] = held_execution
            value["execution"] = None
            value["native_session_id"] = prior_session if old["execution"] is None and spec["workspace"] == prior_workspace else None
        elif operation == "checkpoint":
            coordination.checkpoint(value,request)
        elif operation == "continue":
            coordination.continue_original(value,request)
        elif operation in ("collect-result", "review-result"):
            changed=coordination.collect_result(value,{"path":request.get("path"),"sha256":request.get("sha256")},review=operation=="review-result")
            if not changed:return NO_CHANGE
        elif operation == "block":
            key = request.get("step")
            require(key in value["steps"] and text(request.get("condition")) and text(request.get("evidence_ref")), "block needs step, exact condition and evidence")
            value["steps"][key].update(state="blocked", wait={"kind": "external_condition", "condition": request["condition"], "evidence_ref": request["evidence_ref"]})
            value["accepted"] = False
            value["review"] = value["loaded"] = None
            # Preserve an already-dispatched outcome separately. Blocking can't
            # undo its effects, and a late PASS must not reopen this branch.
            if value["execution"] and value["execution"]["step"] == key:
                value["steps"][key]["unresolved_execution"] = value["execution"]
                value["history"].append({"blocked_inflight": value["execution"], "condition": request["condition"], "evidence_ref": request["evidence_ref"]})
                value["execution"] = None
            remember_restriction(value, key)
        elif operation == "approval":
            key = request.get("step")
            require(key in value["steps"], "unknown approval step")
            require(request.get("decision") in ("pending", "approved", "denied"), "invalid explicit decision")
            require(text(request.get("source_ref")), "approval decision needs its original user/source reference")
            # This records a business decision; it never answers native tool
            # approval requests or changes the worker's permissions.
            require(request["decision"] != "approved", "this CLI adapter cannot authenticate a new human approval; keep it pending and use the original supported decision channel")
            value["steps"][key]["approval"] = {"decision": request["decision"], "source_ref": request["source_ref"],
                                               "input_revision": value["input_revision"]}
            if request["decision"] == "denied":
                value["steps"][key].update(state="blocked", wait={"kind": "explicit_denial", "source_ref": request["source_ref"]})
                value["accepted"] = False
                value["review"] = value["loaded"] = None
                if value["execution"] and value["execution"]["step"] == key:
                    value["steps"][key]["unresolved_execution"] = value["execution"]
                    value["history"].append({"denied_inflight": value["execution"], "source_ref": request["source_ref"]})
                    value["execution"] = None
                remember_restriction(value, key)
        elif operation == "delivery":
            require(request.get("event") in ("sent", "read", "user_accepted", "user_rejected"), "unknown delivery fact")
            require(text(request.get("source_ref")), "delivery fact needs an observed source")
            event = request["event"]
            if event == "sent":
                value["delivery"]["sent"] = {"source_ref": request["source_ref"]}
            elif event == "read":
                value["delivery"]["unverified_read_observation"] = {"source_ref": request["source_ref"], "authentication": "not_established"}
            else:
                value["delivery"]["unverified_decision_observation"] = {"decision": event, "source_ref": request["source_ref"], "authentication": "not_established"}
        elif operation == "reconcile":
            key = request.get("step")
            require(key in value["steps"] and text(request.get("source_ref")), "reconciliation needs the exact step and observed effect source")
            step = value["steps"][key]
            require(step["state"] in ("reconcile", "running", "blocked"), "step has no unresolved effect or failure")
            require((step.get("wait") or {}).get("kind") not in ("external_condition", "explicit_denial"),
                    "effect readback cannot revoke an explicit block or denial; retain the original decision")
            observed = enforce_constraints(value, "reconcile", key)[key]
            require(not observed["blocked"], "effect readback cannot revoke current object constraint")
            execution = step.get("unresolved_execution") or value.get("execution")
            require(execution and (execution["step"] == key or any(h.get("execution", {}).get("id") == execution["id"] for h in step.get("holds", []) if isinstance(h.get("execution"), dict))), "missing original execution identity")
            require_stopped(execution)
            if execution.get("session_id"):
                require(any(e.get("execution_id") == execution["id"] and e.get("status") == "completed" for e in step["evidence"]),
                        "original native outcome is unconfirmed; query it through resume before passive acceptance")
            checks = next(s for s in value["spec"]["steps"] if s["id"] == key)["checks"]
            require(all(c["kind"] == "file" for c in checks), "automatic reconciliation only reads exact file effects; command/message checks need original native result evidence")
            # Recovery never calls the worker. Verify actual current effects.
            prior_execution = value["execution"]
            value["execution"] = {**execution, "input_revision": value["input_revision"], "acceptance_sha256": value["acceptance_sha256"]}
            checked = validate(value, key, "")
            require(checked["passed"], "effect not established; keep unresolved and inspect the original native session, do not replay")
            step.update(state="verified", validation=checked, wait=None)
            step.pop("unresolved_execution", None)
            value["history"].append({"reconciled_execution": execution, "source_ref": request["source_ref"], "validation": checked})
            value["execution"] = None if prior_execution and prior_execution["id"] == execution["id"] else prior_execution
        elif operation == "review":
            require(all(s["state"] == "verified" for s in value["steps"].values()), "review cannot replace missing task results")
            path = Path(request.get("report_path", ""))
            require(path.is_absolute() and isinstance(request.get("report_sha256"), str) and SHA.fullmatch(request["report_sha256"]), "invalid review reference")
            raw = cf.read_file(cf.safe_path(path.parent, path.name))
            require(raw is not None and len(raw) <= LIMIT and digest(raw) == request["report_sha256"], "review report bytes mismatch")
            report = json.loads(raw)
            if any(value["spec"].get(k) for k in ("project", "assignment", "request_identity")):
                coordination.bound_report(value, report)
            require(isinstance(report, dict) and report.get("task_input_sha256") == value["acceptance_sha256"], "review is for a different task input")
            require(report.get("verdict") == "PASS" and text(report.get("reviewer_thread")) and text(report.get("candidate_tree")), "review is not a passing independent candidate report")
            require(re.fullmatch(r"[0-9a-f]{40}", report["candidate_tree"]), "invalid reviewed candidate tree")
            sessions = {s.get("session_id") for s in value["steps"].values()}
            if value["spec"].get("assignment"):
                sessions.add(value["spec"]["assignment"]["owner"])
            if value["spec"].get("request_identity"):
                sessions.add(value["spec"]["request_identity"]["thread_id"])
            require(report["reviewer_thread"] not in sessions, "worker self-test cannot sign independent review")
            value["review"] = {"path": str(path), "sha256": digest(raw), "candidate_tree": report["candidate_tree"],
                               "reviewer_thread": report["reviewer_thread"], "task_input_sha256": value["acceptance_sha256"],
                               "source_authentication": "original coordinator must verify native reviewer provenance; file labels do not authenticate it"}
        elif operation == "loaded":
            import framework_runtime as runtime
            require(value["review"] is not None, "load evidence needs its exact reviewed candidate")
            revision = request.get("revision")
            require(isinstance(revision, str) and re.fullmatch(r"[0-9a-f]{40}", revision), "invalid release revision")
            f = request["framework"]
            active = runtime.active(f)
            require(active and active["revision"] == revision, "reviewed release is not active")
            runtime.validate(runtime.version_path(f, revision), active["manifest_sha256"])
            tree = cf.git(f.repo, "rev-parse", revision + "^{tree}")
            require(tree == value["review"]["candidate_tree"], "installed release tree differs from independent review")
            value["loaded"] = {"revision": revision, "tree": tree, "manifest_sha256": active["manifest_sha256"],
                               "review_sha256": value["review"]["sha256"], "task_input_sha256": value["acceptance_sha256"]}
        elif operation == "accept":
            coordination.validate_current_result(value)
            result_state=coordination.result_state(value)
            require(result_state is None or not result_state["pending"], "original user obligations remain; finite program PASS is insufficient")
            require(not any(s.get("continuation") and not s["continuation"]["resolved"] for s in value["steps"].values()), "original continuation outcome/effect remains unresolved")
            require(value["execution"] is None, "execution still unresolved")
            observations = enforce_constraints(value, "accept")
            require(not any(o["blocked"] for o in observations.values()), "current object constraints prevent acceptance")
            for step in value["steps"].values():
                checked = step.get("validation")
                require(step["state"] == "verified" and isinstance(checked, dict) and checked.get("passed") is True and
                        checked.get("validator") == "task_runtime.validate/v1" and checked.get("input_revision") == value["input_revision"] and
                        checked.get("acceptance_sha256") == value["acceptance_sha256"], "mandatory current acceptance evidence missing")
            require(not value["spec"].get("review_required") or value["review"] is not None, "independent candidate review missing")
            require(not value["spec"].get("load_required") or value["loaded"] is not None, "merged candidate loading evidence missing")
            if value["review"] is not None:
                review_path = Path(value["review"]["path"])
                require(digest(cf.read_file(cf.safe_path(review_path.parent, review_path.name)) or b"") == value["review"]["sha256"], "review bytes changed before acceptance")
            if value["spec"].get("load_required"):
                import framework_runtime as runtime
                from types import SimpleNamespace
                target = SimpleNamespace(codex_home=store.root.parent)
                active = runtime.active(target)
                require(active and active["revision"] == value["loaded"]["revision"] and active["manifest_sha256"] == value["loaded"]["manifest_sha256"], "loaded release changed before acceptance")
                runtime.validate(runtime.version_path(target, active["revision"]), active["manifest_sha256"])
            if value["spec"].get("artifact"):
                coordination.artifact(value)
            # Passive effects are checked again without rerunning commands or
            # model actions; old validation can't bless a changed deliverable.
            for spec in value["spec"]["steps"]:
                for check in spec["checks"]:
                    if check["kind"] == "file":
                        require(checked_file(value, spec["id"], check)["passed"], "verified artifact changed before acceptance")
            final = {s["id"]: assessment(value, s["id"], observations[s["id"]]["scope"]) for s in value["spec"]["steps"]}
            require(not any(o["blocked"] for o in final.values()), "object changed during acceptance; inspect without replay")
            value["accepted"] = True
            value["acceptance_observation"] = {"observed_at": time.time(), "input_revision": value["input_revision"],
                                               "acceptance_sha256": value["acceptance_sha256"],
                                               "scope_sha256": {k: digest(encoded(o["scope"])) for k,o in final.items()},
                                               "guarantee": "coordinator bounded observation; not OS isolation, an atomic multi-file snapshot, or future immutability"}
        else:
            raise TaskError("unsupported task operation")
    return store.update(request.get("expected_revision"), apply)



@coordination.scoped
@io_call
def observe_external(store, expected, reference):
    """Validate already produced effects; never fabricate a native worker turn."""
    value = store.read()
    require(value["spec"].get("execution_mode") == "external", "task did not select external effect collection")
    require(value["revision"] == expected and value["execution"] is None, "external collection revision/execution conflict")
    require(isinstance(reference, dict) and set(reference) == {"path", "sha256"}, "invalid external observation reference")
    report = coordination.read_json(value["spec"]["workspace"], reference.get("path"), reference.get("sha256"))
    coordination.bound_report(value, report)
    require(report.get("verdict") in ("PASS", "FAIL", "NOT_RUN"), "invalid external result verdict")
    if report["verdict"] != "PASS":
        def unavailable(current):
            for state in current["steps"].values():
                if state["state"] == "pending":
                    state.update(state="blocked", wait={"kind": "external_result_" + report["verdict"].lower(), "source_ref": reference, "next": "return current failure/not-run to original owner"})
        return store.update(expected, unavailable)
    attempted = False
    unavailable_reason = None
    for spec in value["spec"]["steps"]:
        key = spec["id"]
        if value["steps"][key]["state"] == "verified":
            continue
        # Consume only this step's actual ready route. A scoped refusal,
        # unknown outcome or missing dependency must not swallow other returns.
        if key not in partition(value, selected=key)["ready"]:
            if unavailable_reason is None:
                pending = value["steps"][key]["state"] == "pending" or value["steps"][key].get("coordination_wait", {}).get("state") == "pending"
                unavailable_reason = ("external effect has unresolved relationship or refusal" if pending else
                                      "effect collection cannot clear a refusal/unresolved original execution")
            continue
        selected = {}
        def begin(current):
            observations = enforce_constraints(current, "external_collection", key)
            require(not observations[key]["blocked"] and all(current["steps"][d]["state"] == "verified" for d in coordination.local_dependencies(spec)), "external effect has unresolved relationship or refusal")
            attempts = current["steps"][key].get("verification_attempts", 0)
            require(attempts < current["spec"].get("max_runs", 3), "external verification attempt bound")
            execution = {"id": uuid.uuid4().hex, "controller_pid": os.getpid(), "worker_pid": None, "session_id": None,
                         "step": key, "input_revision": current["input_revision"], "acceptance_sha256": current["acceptance_sha256"],
                         "state": "validating", "validation_started": True, "producer_kind": "external_observation", "prompt_authorized": False,
                         "started_at": time.time(), "source_ref": reference}
            execution["dispatch_scope"] = file_scope(current["spec"], spec)
            current["execution"] = execution
            current["steps"][key].update(state="running", verification_attempts=attempts + 1)
            selected.update(execution)
        value = store.update(value["revision"], begin, reservation=True)
        attempted = True
        checked = validate(value, key, "")
        def consume(current):
            require(current["execution"] and current["execution"]["id"] == selected["id"], "external result superseded")
            constraints = enforce_constraints(current, "external_commit", key, checked.get("scope_after"))[key]
            state = current["steps"][key]
            state["validation"] = checked
            state["external_evidence"] = {"reference": reference, "identity": coordination.bound_report(current, report), "origin_authentication": "actual native source must be checked by original coordinator; fields do not authenticate it"}
            if checked["passed"] and not constraints["blocked"]:
                state.update(state="verified", wait=None)
            else:
                possible = any(c.get("validator_possible_effects") for c in checked["checks"])
                state.update(state="reconcile" if possible else "blocked", wait={"kind": "external_validation_failed", "checks": checked["checks"], "next": "original owner diagnoses; no automatic replay"})
                if possible:state["unresolved_execution"] = copy.deepcopy(current["execution"])
            current["history"].append({"external_observation": copy.deepcopy(current["execution"]), "validation": checked, "source_ref": reference})
            require(len(current["history"]) <= 64, "task history bound reached")
            current["execution"] = None
        value = execution_update(store, value["revision"], selected["id"], consume)
    complete = all(state["state"] == "verified" for state in value["steps"].values())
    require(attempted or complete, unavailable_reason or "external effect has unresolved relationship or refusal")
    if complete and not value["spec"].get("review_required") and not value["spec"].get("load_required") and (coordination.result_state(value) is None or not coordination.result_state(value)["pending"]):
        value = change(store, {"operation": "accept", "expected_revision": value["revision"]})
    return value


def dispatch(codex_home):
    import sys
    try:
        raw = sys.stdin.buffer.read(LIMIT + 1)
        require(len(raw) <= LIMIT, "request too large")
        request = json.loads(raw)
        require(isinstance(request, dict), "request must be an object")
        operation = request.get("operation")
        if operation in ("coordinate", "work"):
            result = coordination.workset(codex_home, request)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 2 if operation == "work" and not result["overall_complete"] else 0
        if operation == "delivery-view":
            print(json.dumps(coordination.delivery_view(request), ensure_ascii=False, indent=2))
            return 0
        task_id = request.get("task_id")
        store = Store(codex_home, task_id)
        if operation == "register":
            value = store.create(task_id, request.get("spec"))
        elif operation == "status":
            value = store.read()
        elif operation == "run":
            value = run(store, request.get("expected_revision"), request.get("max_turns", 3))
        elif operation == "resume":
            value = resume(store, request.get("max_turns", 3))
        elif operation in ("amend", "block", "approval", "delivery", "reconcile", "review", "accept", "checkpoint", "continue", "collect-result", "review-result"):
            value = change(store, request)
        elif operation == "verify-effects":
            value = observe_external(store, request.get("expected_revision"), {"path": request.get("path"), "sha256": request.get("sha256")})
        elif operation == "verify-return":
            value = store.read()
            report = coordination.read_json(value["spec"]["workspace"], request.get("path"), request.get("sha256"))
            bound = coordination.bound_report(value, report)
            print(json.dumps({"status": "bound", "identity": bound, "completion_scope": "current request report binding only; no acceptance or permission granted"}, ensure_ascii=False))
            return 0
        elif operation == "loaded":
            from codex_framework import Framework
            import framework_runtime as runtime
            request["framework"] = Framework(runtime.default_repo(), Path(codex_home).parent, codex_home)
            value = change(store, request)
        else:
            raise TaskError("unsupported task operation")
        with coordination.context(store.root):
            print(json.dumps(view(value), ensure_ascii=False, indent=2))
        return 2 if operation in ("run", "resume", "verify-effects") and not value["accepted"] else 0
    except (TaskError, cf.SyncError, OSError, ValueError, TypeError) as exc:
        print(json.dumps({"status": "task_error", "reason": str(exc)}, ensure_ascii=False))
        return 1
