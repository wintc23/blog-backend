import urllib.parse
import urllib.request
import json
import uuid
import os
import sys

from flask import g, jsonify, request, current_app
from . import api
from .errors import *
from .. import db
from ..models import User, Permission, Tag, Post, Role, PostType, Comment, Message, Like
from .decorators import login_required
from sqlalchemy import and_

@api.route('/github-login/<code>')
def github_login(code):
  secret = current_app.config['FLASK_GITHUB_SECRET']
  client_id = current_app.config['FLASK_GITHUB_CLIENT_ID']
  url = 'https://github.com/login/oauth/access_token'
  data = {
    'code': code,
    'client_id': client_id,
    'client_secret': secret
  }
  params = urllib.parse.urlencode(data).encode('utf-8')
  headers = {
    'User-Agent': 'Mozilla/4.0 (compatible; MSIE 5.5; Windows NT)',
    'Accept': 'application/json'
  }
  req = urllib.request.Request(url, params, headers)
  html = urllib.request.urlopen(req).read().decode('utf-8')
  access_data = json.loads(html)
  print(access_data)
  if access_data.get('error', ''):
    return bad_request('链接已失效，请重新登录', True)
  access_token = access_data['access_token']
  req2 = urllib.request.Request(url='https://api.github.com/user?access_token='+access_token, headers=headers)
  html2 = urllib.request.urlopen(req2).read().decode('utf-8')
  info = json.loads(html2)
  id_string = 'github' + str(info['id'])
  user = User.query.filter_by(id_string=id_string).first()
  if not user:
    avatar_url = info['avatar_url']
    if avatar_url:
      filename = str(uuid.uuid1())
      dirname, _ = os.path.split(os.path.abspath(sys.argv[0]))
      fold_path = dirname + '/../files/avatar/'
      if not os.path.exists(fold_path):
        os.makedirs(fold_path)
      upload_path = fold_path + filename
      urllib.request.urlretrieve(avatar_url, upload_path)
      res = urllib.request.urlretrieve(avatar_url, upload_path)
      avatar = filename
    register_info = {
      'username': info['login'],
      'id_string': id_string,
      'avatar': avatar
    }
    if info['email']:
      register_info['email']
    user = User(**register_info)
    try:
      db.session.add(user)
      db.session.commit()
    except:
      db.session.rollback()
      response = jsonify({ 'error': 'create user error', 'message': '创建用户失败，请重新登录' })
      response.status_code = 500
      return response
    db.session.add(user)
  token = user.generate_auth_token(3600 * 24 * 30)
  return jsonify({ 'token': token })

@api.route('/get-user/<user_id>')
def get_user_info(user_id):
  user = User.query.get(user_id)
  if not user:
    return not_found('获取不到用户信息')
  return jsonify(user.to_json())

@api.route('/get-self/')
@login_required
def get_self_info():
  if not g.current_user:
    return bad_request('未找到用户信息')
  return jsonify(g.current_user.get_detail())

@api.route('/check-admin/')
def checkAdmin():
  return jsonify({ 'admin': bool(g.current_user and g.current_user.can(Permission.ADMIN))})

@api.route('/get-user-info/')
def get_admin_info():
  role = Role.query.filter_by(name = 'Administrator').first()
  if not role:
    return not_found('未找到管理员信息')
  post_type = PostType.query.filter_by(special = 1).first()
  if not post_type:
    return not_found('未找到管理员信息')
  user = role.users.first()
  json = user.to_json()
  # post_count = Post.query.filter(and_(Post.hide == False, Post.type_id != post_type.id)).count()
  json['post_count'] = Post.query.count()
  # json['like_count'] = Like.query.count()
  # json['comment_count'] = Comment.query.filter_by(hide = False).count()
  # json['message_count'] = Message.query.filter_by(hide = False).count()

  return jsonify(json)