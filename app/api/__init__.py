from flask import Blueprint, request, g, jsonify, current_app
from itsdangerous import TimedJSONWebSignatureSerializer as Serializer

api = Blueprint('api', __name__)

from . import comments, posts, users, decorators, errors, files, messages, topic, tag, link, sitemap, stat, ai_chat, products, personal_profile, ai_digest, generation, life_moments, email_login, media, image_tools, device_login, albums
from ..models import User, AiAccessKey
from .. import db

@api.before_request
def before_request():
  g.media_cleanup_needed = False
  if request.method == 'OPTIONS':
    return jsonify({ 'success': True })
  authString = request.headers.get('Authorization', '')
  g.current_user = None
  g.current_ai_key = None
  if authString:
    token = authString
    if token.startswith('Bearer '):
      token = token[7:]
    try:
      s = Serializer(current_app.config['SECRET_KEY'])
      data = s.loads(token.encode('utf-8'))
      if data.get('type') == 'ai_key':
        ai_key = AiAccessKey.query.get(data.get('id'))
        if ai_key and ai_key.is_available():
          g.current_ai_key = ai_key
        return
    except:
      pass
    current_user = User.verify_auth_token(token)
    if current_user:
      g.current_user = current_user

@api.after_request
def after_request(response):
  try:
    db.session.commit()
  except:
    db.session.rollback()
    response = errors.server_error('服务器出现异常', True)
  if getattr(g, 'media_cleanup_needed', False):
    from ..media import cleanup_images
    try:
      cleanup_images(limit=6)
    except Exception:
      db.session.rollback()
      current_app.logger.warning('Image cleanup will retry later')
  return response

from ..media import MediaReferenceError

@api.errorhandler(MediaReferenceError)
def invalid_media(error):
  db.session.rollback()
  return jsonify({'message': str(error), 'notify': True}), 400

@api.teardown_request
def dbsession_clean(exception=None):
  try:
    db.session.remove()
  finally:
    pass
