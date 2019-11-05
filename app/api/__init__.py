from flask import Blueprint, request, g, jsonify

api = Blueprint('api', __name__)

from . import comments, likes, posts, users, decorators, errors, files, messages, topic, tag
from ..models import User
from .. import db

@api.before_request
def before_request():
  print('before_request')
  if request.method == 'OPTIONS':
    return jsonify({ 'success': True })
  authString = request.headers.get('Authorization', '')
  g.current_user = None
  if authString:
    current_user = User.verify_auth_token(authString)
    if current_user:
      g.current_user = current_user

@api.after_request
def after_request(response):
  try:
    db.session.commit()
  except:
    response = errors.server_error('服务器出现异常', True)
  return response

@api.teardown_request
def dbsession_clean(exception=None):
  try:
    db.session.remove()
  finally:
    pass