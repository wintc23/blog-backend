import os
import sys
import uuid
import time
import re

from qiniu import Auth
from . import api
from flask import g, jsonify, request, send_from_directory, current_app
from .decorators import permission_required
from ..models import Permission
from ..qiniu import get_token

@api.route('/get-file/')
def get_file():
  dirname, _ = os.path.split(os.path.abspath(sys.argv[0]))
  dirpath = dirname + '/../files/'
  filename = request.args.get('filename')
  path = request.args.get('path')
  if filename != 'default' and path:
    dirpath += path + '/'
  return send_from_directory(dirpath, filename)

@api.route('/get-qiniu-token/<filename>')
@permission_required(Permission.ADMIN)
def get_qiniu_token(filename):
  if not re.fullmatch(r'[a-f0-9]{32}', filename):
    return jsonify({'message': '文件名不正确'}), 400
  video = request.args.get('kind') == 'video'
  token = get_token(filename, max_size=(50 if video else 5) * 1024 * 1024,
                    mime_limit='video/mp4;video/webm' if video else 'image/jpeg;image/png;image/webp')
  domain = current_app.config['QI_NIU_LINK_URL']
  return jsonify({ 'token': token, 'domain': domain })

@api.route('/save-image/', methods = ['PUT'])
@permission_required(Permission.ADMIN)
def save_post_image():
  from .media import upload_image
  return upload_image()
