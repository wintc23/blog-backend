#!/usr/bin/env bash
# Start the local image queue and optional SSH connection to the configured CPA host.
set -euo pipefail
cd "$(dirname "$0")"
if [[ -z "${CONTENT_CPA_API_KEY:-}" ]]; then
  echo 'Image tools: CONTENT_CPA_API_KEY is missing; generation remains unavailable.'
  exit 0
fi
if [[ -n "${IMAGE_TOOLS_CPA_SSH_HOST:-}" ]]; then
  cpa_local_port=$(venv/bin/python -c 'import os; from urllib.parse import urlsplit; print(urlsplit(os.environ["CONTENT_CPA_BASE_URL"]).port)')
  if ! lsof -nP -iTCP:"$cpa_local_port" -sTCP:LISTEN >/dev/null 2>&1; then
    ssh -fNT -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
      -L "127.0.0.1:${cpa_local_port}:127.0.0.1:${IMAGE_TOOLS_CPA_REMOTE_PORT:-8317}" "$IMAGE_TOOLS_CPA_SSH_HOST"
  fi
fi
worker_pid_file=${IMAGE_TOOLS_WORKER_PID_FILE:-/tmp/awesome-image-tools-worker.pid}
worker_log=${IMAGE_TOOLS_WORKER_LOG:-/tmp/awesome-image-tools-worker.log}
if [[ -f "$worker_pid_file" ]]; then
  worker_pid=$(cat "$worker_pid_file")
  if [[ "$worker_pid" =~ ^[0-9]+$ ]] && kill -0 "$worker_pid" 2>/dev/null && ps -p "$worker_pid" -o args= | grep -q 'image_tools.py worker'; then
    echo 'Image tools worker is already running.'
    exit 0
  fi
fi
nohup venv/bin/python -u image_tools.py worker >> "$worker_log" 2>&1 < /dev/null &
echo "$!" > "$worker_pid_file"
echo "Image tools worker started (PID $!)."
