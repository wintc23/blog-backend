# !/bin/bash
source ../env.sh
source venv/bin/activate
gunicorn --worker-class eventlet -w 1 -b:5001 main:app