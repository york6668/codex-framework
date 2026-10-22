"""Publish bounded, credential-free observations for this local resource manager."""

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

from common import ROOT, config
import pymysql
import pymongo
import redis

SERVICES = {"mysql": 3306, "mongo": 27017, "redis": 6379}
MAX_BYTES = 8192
OBSERVED_ACTIONS = frozenset({"up", "stop", "restart", "status", "sync"})


def atomic_json(path, value):
    path = Path(path)
    payload = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
    if len(payload) > MAX_BYTES:
        raise ValueError("Observation exceeds the bounded source size")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".observation-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def inspect_service(service):
    # Only an explicit projection leaves Docker; environment and mounts are not read.
    template = '{"id":{{json .Id}},"state":{{json .State}},"ports":{{json .NetworkSettings.Ports}},"project":{{json (index .Config.Labels "com.docker.compose.project")}}}'
    result = subprocess.run(
        ["docker", "inspect", "--format", template, "mi-local-" + service],
        capture_output=True, text=True, timeout=8, check=True,
    )
    item = json.loads(result.stdout)
    state = item["state"]
    port = str(SERVICES[service])
    owned = item["project"] == "mi-local" and item["ports"].get(port + "/tcp") == [
        {"HostIp": "127.0.0.1", "HostPort": port}
    ]
    healthy = state.get("Running") and not state.get("OOMKilled") and state.get("Health", {}).get("Status") == "healthy"
    return {
        "service": service, "resource_id": item["id"],
        "state": state.get("Status", "unknown"), "owned_local_binding": owned,
        "container_healthy": bool(healthy), "oom_killed": bool(state.get("OOMKilled")),
    }


def ping_service(service):
    options = dict(config("local")[service])
    if options.get("host") != "127.0.0.1" or options.get("port") != SERVICES[service]:
        raise ValueError("Observation is restricted to the configured local service")
    if service == "mysql":
        with pymysql.connect(**options, connect_timeout=3, read_timeout=3, write_timeout=3) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                if cursor.fetchone()[0] != 1:
                    raise RuntimeError("MySQL read probe failed")
    elif service == "mongo":
        with pymongo.MongoClient(**options, serverSelectionTimeoutMS=3000, connectTimeoutMS=3000, socketTimeoutMS=3000) as client:
            client.admin.command("ping")
            if not client.admin.command("isMaster").get("ismaster"):
                raise RuntimeError("Mongo primary unavailable")
    else:
        if options.get("db") != 0:
            raise ValueError("Unexpected local Redis database")
        client = redis.Redis(**options, socket_connect_timeout=3, socket_timeout=3)
        try:
            if not client.ping():
                raise RuntimeError("Redis ping failed")
        finally:
            client.connection_pool.disconnect()


def collect(identity, inspect=inspect_service, ping=ping_service):
    rows = []
    for service in SERVICES:
        try:
            row = inspect(service)
            if row["owned_local_binding"] and row["container_healthy"]:
                ping(service)
                row["probe"] = "PASS"
            else:
                row["probe"] = "FAIL"
        except Exception as error:
            # Exception messages may contain connection details: never persist them.
            row = {"service": service, "probe": "UNKNOWN", "error_type": type(error).__name__}
        rows.append(row)
    status = "HEALTHY" if all(row["probe"] == "PASS" for row in rows) else "UNHEALTHY" if any(row["probe"] == "FAIL" for row in rows) else "UNKNOWN"
    return dict(identity, status=status, observed_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                scope="mi-local mysql/mongo/redis only", resources=rows)


def publish(operation=None):
    codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    root = codex_home / "workstation"
    source = root / "mi-local" / "observation.json"
    manifest_path = root / "observation-sources.json"
    # Withdraw the old claim before any fallible discovery or new state write.
    # Retain one bounded prior observation for diagnosing an interrupted attempt.
    if source.exists():
        os.replace(source, source.with_name("previous-observation.json"))
    atomic_json(source, {"status": "OBSERVING", "observed_at": dt.datetime.now(dt.timezone.utc).isoformat()})
    node = subprocess.check_output(["scutil", "--get", "LocalHostName"], text=True, timeout=3).strip()
    task = os.environ.get("CODEX_THREAD_ID", "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,48}", node) or not re.fullmatch(r"[0-9a-f-]{36}", task):
        raise ValueError("A stable node and native task identity are required")
    version = hashlib.sha256(Path(__file__).read_bytes() + (ROOT / "scripts/dbctl.py").read_bytes()).hexdigest()
    identity = {"project_id": "mi-local", "task_ref": "codex:thread:" + task, "node_id": node, "version": version}
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {
        "schema": "workstation-entry-v1", "node_id": node, "sources": []}
    if manifest.get("schema") != "workstation-entry-v1" or manifest.get("node_id") != node:
        raise ValueError("Existing manifest belongs to another node or schema")
    specs = [item for item in manifest["sources"] if item.get("id") != "mi-local-services"]
    specs.append({"id": "mi-local-services", "path": str(source), "status_path": "status",
                  "time_path": "observed_at", "max_age_seconds": 900,
                  "ok_values": ["HEALTHY"], "failure_values": ["UNHEALTHY"], "identity": identity})
    if len(specs) > 8:
        raise ValueError("Observation source limit exceeded")
    manifest["sources"] = specs
    # Invalidate the prior healthy result before collecting: a killed probe stays UNKNOWN.
    atomic_json(source, dict(identity, status="OBSERVING", observed_at=dt.datetime.now(dt.timezone.utc).isoformat()))
    atomic_json(manifest_path, manifest)
    observation = collect(identity)
    if operation is not None:
        observation["operation"] = operation
    atomic_json(source, observation)
    print(json.dumps({"status": observation["status"], "scope": observation["scope"],
                      "source": str(source), "observed_at": observation["observed_at"]}, ensure_ascii=False))
    return 0 if observation["status"] == "HEALTHY" else 2


def main(operation=None):
    try:
        return publish(operation)
    except Exception as error:
        print(json.dumps({"status": "UNKNOWN", "error_type": type(error).__name__}))
        return 2


def run_action(action, remaining, execute):
    """Keep the manager's outcome and refresh observation after supported actions."""
    if action == "observe":
        return main()
    result, observed = 1, 0
    try:
        result = execute(action, remaining)
    finally:
        if action in OBSERVED_ACTIONS:
            observed = main({"action": action, "exit_code": result})
    return result or observed


if __name__ == "__main__":
    raise SystemExit(main())
