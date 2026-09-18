#!/bin/bash
set -eu
: "${LARK_BRIDGE_ENV_FILE:?Set LARK_BRIDGE_ENV_FILE to your environment file}"
bridge_project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
set -a
source "$LARK_BRIDGE_ENV_FILE"
set +a
cd "$bridge_project_dir"
exec "${LARK_BRIDGE_PYTHON:-python3}" "$bridge_project_dir/lark_admin.py" run
