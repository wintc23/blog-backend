#!/bin/bash
set -e
cd "$(dirname "$0")"
source ../env.sh
if [ -f ../env.local.sh ]; then
  source ../env.local.sh
fi

if [ "${FLASK_CONFIG:-development}" != "production" ]; then
  bash ./start-db-tunnel.sh
  bash ./start-image-tools-local.sh
fi

source venv/bin/activate
gunicorn --worker-class eventlet -w 1 -b 127.0.0.1:5001 main:app
