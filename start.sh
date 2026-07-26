#!/bin/bash

if [ -x ../start-db-tunnel.sh ]; then
  ../start-db-tunnel.sh
fi

source ../env.sh
if [ -f ../env.local.sh ]; then
  source ../env.local.sh
fi
source venv/bin/activate
gunicorn --worker-class eventlet -w 1 -b 127.0.0.1:5001 main:app
