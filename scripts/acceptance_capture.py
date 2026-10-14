"""Bounded capture for a manually invoked desktop CLI acceptance run.

The CLI's JSON event stream is stdout. Its diagnostic logger writes stderr.
They have different meanings and must not share a failure budget.
"""

import hashlib
import os
from pathlib import Path
import selectors
import subprocess
import time


def _write_private(path, value):
    path = Path(path)
    with path.open("wb") as output:
        os.chmod(path, 0o600)
        output.write(value)


def run_bounded(argv, *, cwd, stdout_path, stderr_path, timeout_seconds=140,
                stdout_limit=128 * 1024, stderr_limit=64 * 1024, env=None):
    """Run once, retaining all bounded JSON stdout and a head/tail stderr sample.

    Verbose diagnostics cannot terminate a healthy event stream. A timeout or
    oversized stdout is a failed acceptance, and the process is killed.
    The caller controls whether argv may start a model; this function never retries.
    """
    if min(timeout_seconds, stdout_limit, stderr_limit) <= 0:
        raise ValueError("capture bounds must be positive")
    if Path(stdout_path).resolve() == Path(stderr_path).resolve():
        raise ValueError("stdout and stderr paths must differ")
    process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, bufsize=0)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    deadline = time.monotonic() + timeout_seconds
    stdout = bytearray()
    stdout_seen = 0
    stderr_head = bytearray()
    stderr_tail = bytearray()
    stderr_seen = 0
    stderr_hash = hashlib.sha256()
    head_limit = stderr_limit // 2
    tail_limit = stderr_limit - head_limit
    reason = None
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                reason = "timeout"
                break
            for key, _ in selector.select(min(remaining, 0.5)):
                chunk = os.read(key.fileobj.fileno(), 8192)
                if not chunk:
                    selector.unregister(key.fileobj)
                elif key.data == "stdout":
                    stdout_seen += len(chunk)
                    room = stdout_limit - len(stdout)
                    stdout.extend(chunk[:room])
                    if stdout_seen > stdout_limit:
                        reason = "stdout_limit"
                        break
                else:
                    stderr_seen += len(chunk)
                    stderr_hash.update(chunk)
                    head_room = head_limit - len(stderr_head)
                    stderr_head.extend(chunk[:head_room])
                    remainder = chunk[head_room:]
                    stderr_tail = (stderr_tail + remainder)[-tail_limit:]
            if reason:
                break
    finally:
        selector.close()
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        process.stdout.close()
        process.stderr.close()
        _write_private(stdout_path, stdout)
        stderr_sample = stderr_head + stderr_tail
        _write_private(stderr_path, stderr_sample)
    return {
        "exit_code": process.returncode,
        "termination_reason": reason,
        "stdout_bytes_seen": stdout_seen,
        "stdout_bytes_saved": len(stdout),
        "stderr_bytes_seen": stderr_seen,
        "stderr_bytes_saved": len(stderr_sample),
        "stderr_truncated": stderr_seen > len(stderr_sample),
        "stderr_sha256": stderr_hash.hexdigest(),
    }
