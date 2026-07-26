#!/bin/bash
source ../env.sh
if [ -f ../env.local.sh ]; then
  source ../env.local.sh
fi

if [ "${FLASK_CONFIG:-development}" != "production" ]; then
  bash ./start-db-tunnel.sh
fi

source venv/bin/activate
gunicorn --worker-class eventlet -w 1 -b 127.0.0.1:5001 main:app
