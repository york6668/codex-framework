"""Bounded native Codex CLI worker. Inherits configuration, auth and permissions.

Does not edit auth/config/hooks, approve tools, or choose an unregistered model.
Raw model/tool transcripts stay in the native session rather than this record.
"""
import hashlib
import json
import os
import selectors
import shutil
import subprocess
import time
import re


def error_summary(error):
    if not isinstance(error, dict):
        return {"shape": type(error).__name__}
    message = error.get("message")
    if isinstance(message, str):
        message = re.sub(r"(?i)bearer\s+[^\s\"']+|sk-[A-Za-z0-9_-]{8,}", "<redacted>", message)[:600]
    return {"message": message, "code": error.get("code"), "codex_error_info": error.get("codexErrorInfo"),
            "original_sha256": hashlib.sha256(json.dumps(error, sort_keys=True).encode()).hexdigest()}


def inspect_session(session_id, execution_id, workspace=None):
    """Read the owned persisted native turn; never resume or send a model prompt."""
    result = {"status": "unknown", "session_id": session_id, "message": "", "error": "native_result_not_established"}
    if not isinstance(session_id, str) or not session_id:
        result["error"] = "original_native_session_identity_missing"
        return result
    binary = shutil.which("codex")
    if not binary:
        result["error"] = "native_codex_executable_missing"
        return result
    child = None
    selector = selectors.DefaultSelector()
    buffer = b""
    try:
        child = subprocess.Popen([binary, "app-server", "--listen", "stdio://"], cwd=workspace,
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        selector.register(child.stdout, selectors.EVENT_READ, "out")
        selector.register(child.stderr, selectors.EVENT_READ, "err")
        def rpc(value):
            nonlocal buffer
            child.stdin.write((json.dumps(value) + "\n").encode()); child.stdin.flush()
            if "id" not in value:
                return {}
            deadline = time.monotonic() + 8
            count = 0
            while time.monotonic() < deadline:
                for key, _ in selector.select(min(.25, max(0, deadline-time.monotonic()))):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    count += len(chunk)
                    if count > 1048576:
                        raise ValueError("bounded_native_read_exceeded")
                    if key.data == "err":
                        continue
                    buffer += chunk
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        if not line:
                            continue
                        response = json.loads(line)
                        if not isinstance(response, dict):
                            raise ValueError("native_read_response_not_object")
                        if response.get("id") == value["id"]:
                            if "error" in response:
                                error = response["error"]
                                raise ValueError("native_read_error_code_" + str(error.get("code") if isinstance(error, dict) else "invalid_shape"))
                            return response.get("result", {})
                if child.poll() is not None:
                    break
            raise ValueError("native_read_no_response")
        rpc({"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "framework_task_recovery", "version": "1"}}})
        rpc({"method": "initialized", "params": {}})
        page = rpc({"id": 2, "method": "thread/turns/list", "params": {"threadId": session_id, "limit": 32, "sortDirection": "desc", "itemsView": "full"}})
        if not isinstance(page, dict) or not isinstance(page.get("data"), list):
            raise ValueError("native_turn_page_invalid")
        marker = "Framework task execution " + execution_id
        matches = []
        for turn in page.get("data", []):
            if not isinstance(turn, dict) or not isinstance(turn.get("items"), list):
                raise ValueError("native_turn_shape_invalid")
            if turn.get("itemsView") not in (None, "full"):
                continue
            for item in turn.get("items", []):
                if not isinstance(item, dict):
                    raise ValueError("native_turn_item_invalid")
                content = item.get("content", [])
                if item.get("type") == "userMessage" and (not isinstance(content, list) or any(not isinstance(block, dict) for block in content)):
                    raise ValueError("native_user_content_invalid")
                if item.get("type") == "userMessage" and any(block.get("type") == "text" and isinstance(block.get("text"), str) and block["text"].split("\n", 1)[0] == marker for block in content):
                    matches.append(turn)
                    break
        if len(matches) != 1:
            result["error"] = "original_native_turn_not_uniquely_found_in_bounded_page"
        else:
            turn = matches[0]
            if not isinstance(turn.get("id"), str) or not turn["id"].strip():
                raise ValueError("native_turn_identity_invalid")
            result["native_turn_id"] = turn["id"]
            finals = [item for item in turn["items"] if item.get("type") == "agentMessage" and item.get("phase") == "final_answer"]
            declines = [item for item in turn["items"] if item.get("type") in ("commandExecution", "fileChange") and item.get("status") == "declined"]
            failures = [item for item in turn["items"] if item.get("type") == "commandExecution" and item.get("exitCode") not in (None, 0)]
            if failures:
                result["tool_failures"] = [{"item_id": item.get("id"), "exit_code": item.get("exitCode")} for item in failures]
            if declines:
                result["status"] = "failed"
                result["error"] = "native_permission_declined"
                result["native_permission_declined"] = True
            elif turn.get("status") == "completed":
                if turn.get("error") is not None:
                    raise ValueError("native_completed_turn_has_error")
                if len(finals) != 1 or ("truncated" in finals[0] and finals[0]["truncated"] is not False):
                    raise ValueError("native_final_missing_ambiguous_or_truncated")
                if not isinstance(finals[0].get("id"), str) or not finals[0]["id"].strip():
                    raise ValueError("native_final_identity_missing")
                message = finals[0].get("text")
                if not isinstance(message, str) or len(message.encode()) > 32768:
                    raise ValueError("native_final_missing_or_too_large")
                result.update(status="completed", message=message, error=None, native_final_id=finals[0]["id"])
            elif turn.get("status") in ("failed", "interrupted"):
                result["status"] = "failed"
                result["error"] = "original_native_turn_" + turn["status"]
                result["native_error"] = error_summary(turn.get("error"))
            else:
                result["error"] = "original_native_turn_still_active"
            result["native_turn_sha256"] = hashlib.sha256(json.dumps(turn, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        result.update(status="unknown", message="", error=type(exc).__name__ + ": " + str(exc))
    finally:
        selector.close()
        if child:
            child.stdin.close()
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                child.terminate()
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill(); child.wait(timeout=3)
            child.stdout.close(); child.stderr.close()
        result["message_sha256"] = hashlib.sha256(result["message"].encode()).hexdigest()
    return result


def execute(prompt, workspace, session_id=None, timeout=180, on_session=None, on_process=None, model=None, reasoning_effort=None):
    binary = shutil.which("codex")
    if not binary:
        return {"status": "failed", "error": "native_codex_executable_missing", "session_id": session_id, "prompt_sent": False}
    options = ["--json"]
    if model is not None:
        options += ["--model", model]
    if reasoning_effort is not None:
        options += ["-c", "model_reasoning_effort=" + json.dumps(reasoning_effort)]
    argv = [binary, "exec", "resume", *options, session_id, "-"] if session_id else [binary, "exec", *options, "--cd", workspace, "-"]
    result = {"argv": argv, "session_id": session_id, "status": "unknown", "message": "", "error": None, "started_at": time.time(), "prompt_sent": False}
    events_hash, stderr_hash = hashlib.sha256(), hashlib.sha256()
    total = 0
    completed = failed = False
    child = None
    selector = selectors.DefaultSelector()
    buffer = b""
    try:
        child = subprocess.Popen(argv, cwd=workspace, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, bufsize=0)
        result["pid"] = child.pid
        if on_process and on_process(child.pid) is False:
            result.update(status="failed", error="coordinator_pre_prompt_blocked")
            return result
        result["prompt_sent"] = True  # Intent before write; a partial/failed write is still a possible dispatch.
        child.stdin.write(prompt.encode() + b"\n")
        child.stdin.close()
        selector.register(child.stdout, selectors.EVENT_READ, "out")
        selector.register(child.stderr, selectors.EVENT_READ, "err")
        end = time.monotonic() + timeout
        while selector.get_map() and time.monotonic() < end:
            for key, _ in selector.select(min(.25, max(0, end-time.monotonic()))):
                chunk = os.read(key.fd, 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                total += len(chunk)
                if total > 4194304:
                    raise ValueError("bounded_native_output_exceeded")
                if key.data == "err":
                    stderr_hash.update(chunk)
                    continue
                events_hash.update(chunk)
                buffer += chunk
                if len(buffer) > 262144:
                    raise ValueError("native_event_line_too_large")
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    if not line.strip():
                        continue
                    event = json.loads(line)
                    if not isinstance(event, dict):
                        raise ValueError("native_event_not_object")
                    kind = event.get("type")
                    if kind == "thread.started":
                        identity = event.get("thread_id")
                        if not isinstance(identity, str) or not identity:
                            raise ValueError("native_thread_identity_missing")
                        if result["session_id"] and result["session_id"] != identity:
                            raise ValueError("native_session_changed")
                        result["session_id"] = identity
                        if on_session and on_session(identity) is False:
                            result.update(status="unknown", error="coordinator_scope_changed_after_prompt")
                            return result
                    elif kind == "turn.completed":
                        completed = True
                        result["usage"] = event.get("usage")
                    elif kind in ("turn.failed", "error"):
                        failed = True
                        result["error"] = kind
                        result["native_error"] = error_summary(event.get("error", {"message": event.get("message")}))
                    elif kind == "item.completed":
                        item = event.get("item", {})
                        if isinstance(item, dict) and item.get("type") in ("command_execution", "file_change") and item.get("status") == "declined":
                            failed = True
                            result["error"] = "native_permission_declined"
                            # Later terminal/transport errors describe another
                            # fact; they cannot erase this observed refusal.
                            result["native_permission_declined"] = True
                        elif isinstance(item, dict) and item.get("type") == "agent_message":
                            message = item.get("text")
                            if not isinstance(message, str) or len(message.encode()) > 32768:
                                raise ValueError("native_final_missing_or_too_large")
                            result["message"] = message
                        elif isinstance(item, dict) and item.get("type") == "command_execution":
                            if item.get("status") == "declined":
                                failed = True
                                result["error"] = "native_permission_declined"
                            elif item.get("exit_code") not in (None, 0):
                                result.setdefault("tool_failures", []).append({"item_id": item.get("id"), "exit_code": item.get("exit_code"),
                                    "command_sha256": hashlib.sha256(str(item.get("command", "")).encode()).hexdigest()})
        if selector.get_map():
            result["error"] = "native_deadline_reached_effect_unknown"
        else:
            child.wait(timeout=3)
            if completed and not failed and child.returncode == 0 and result["session_id"]:
                result["status"] = "completed"
            elif failed or child.returncode:
                result["status"] = "failed"
                result["error"] = result["error"] or "native_process_failed"
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        result["error"] = type(exc).__name__ + ": " + str(exc)
    finally:
        selector.close()
        if child:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=3)
            result["exit"] = child.returncode
            if not child.stdin.closed:child.stdin.close()
            child.stdout.close(); child.stderr.close()
        result["events_sha256"] = events_hash.hexdigest()
        result["stderr_sha256"] = stderr_hash.hexdigest()
        result["message_sha256"] = hashlib.sha256(result["message"].encode()).hexdigest()
        result["ended_at"] = time.time()
    return result
