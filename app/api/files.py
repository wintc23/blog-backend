import os
import sys
import uuid
from . import api
from flask import g, jsonify, request, send_from_directory

@api.route('/get-file/')
def get_file():
  dirname, _ = os.path.split(os.path.abspath(sys.argv[0]))
  dirpath = dirname + '/../files/'
  filename = request.args.get('filename')
  path = request.args.get('path')
  if path:
    dirpath += path + '/'
  return send_from_directory(dirpath, filename)