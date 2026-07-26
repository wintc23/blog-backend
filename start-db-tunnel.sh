#!/bin/bash
set -e

if lsof -nP -iTCP:13306 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "MySQL SSH tunnel is already running on 127.0.0.1:13306"
  exit 0
fi

ssh -fN \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -L 127.0.0.1:13306:127.0.0.1:3306 \
  root@wintc.top

echo "MySQL SSH tunnel started on 127.0.0.1:13306"
