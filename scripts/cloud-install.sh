#!/bin/sh
set -eu
framework_repo=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec python3 "$framework_repo/scripts/codex_framework.py" install
