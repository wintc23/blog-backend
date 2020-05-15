import urllib.parse
import urllib.request
import json
import uuid
import os
import sys
import re
import ssl

from flask import g, jsonify, request, current_app
from . import api
from .errors import *
from .. import db
from ..models import User, Permission, Tag, Post, Role, PostType, Comment, Message, Like
from .decorators import login_required, permission_required
from sqlalchemy import and_
from ..email import send_email
from ..qiniu import get_token
from qiniu import put_data

ssl._create_default_https_context = ssl._create_unverified_context

def save_file(url):
  try:
    req = urllib.request.Request(url)
    res = urllib.request.urlopen(req)
    filename = str(uuid.uuid4()).replace('-', '')
    token = get_token(filename)
    ret, _ = put_data(token, filename, data = res.read())
    print(ret)
    return ret.get('key')
  except Exception as e:
    print(e, '~~~~~~~~')
    return ''

def save_all_user_avatar(base):
  userList = User.query.all()
  defaultname = ''
  for user in userList:
    if user.avatar == 'default':
      if not defaultname:
        defaultname = save_file(base + '/get-file/?path=avatar&filename=' + user.avatar)
      user.avatar = defaultname
    else:
      user.avatar = save_file(base + '/get-file/?path=avatar&filename=' + user.avatar)
    db.session.add(user)
  try:
    db.session.commit()
  except Exception as e:
    db.session.rollback()

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
    'Accept': 'application/json',
  }
  req = urllib.request.Request(url, params, headers)
  html = urllib.request.urlopen(req).read().decode('utf-8')
  access_data = json.loads(html)
  print(access_data)
  if access_data.get('error', ''):
    return bad_request('链接已失效，请重新登录', True)
  access_token = access_data['access_token']
  headers['Authorization'] = 'token ' + access_data['access_token']
  print('access_token', access_data['access_token'])
  req2 = urllib.request.Request(url='https://api.github.com/user', headers=headers)
  html2 = urllib.request.urlopen(req2).read().decode('utf-8')
  info = json.loads(html2)
  print('userinfo', info)
  id_string = 'github' + str(info['id'])
  user = User.query.filter_by(id_string=id_string).first()
  if not user:
    avatar = save_file(info['avatar_url'])
    if not avatar:
      return server_error('获取github用户信息失败', True)
    register_info = {
      'username': info['login'],
      'id_string': id_string,
      'avatar': avatar
    }
    if info['email']:
      register_info['email'] = info['email']
    user = User(**register_info)
    try:
      db.session.add(user)
      db.session.commit()
      reciver = current_app.config['FLASK_ADMIN']
      send_email(reciver, '用户注册', mail_type = 1, username = info['login'])
    except Exception as e:
      db.session.rollback()
      response = jsonify({ 'error': 'create user error', 'message': '创建用户失败，请重新登录' })
      response.status_code = 500
      return response
  token = user.generate_auth_token(3600 * 24 * 30)
  return jsonify({ 'token': token })

@api.route('/qq-login/', methods=["POST"])
def qq_login():
  code = request.json.get('code', '')
  redirect = request.json.get('redirect', '')
  secret = current_app.config['FLASK_QQ_SECRET']
  client_id = current_app.config['FLASK_QQ_CLIENT_ID']
  url = 'https://graph.qq.com/oauth2.0/token'
  data = {
    'grant_type': 'authorization_code',
    'client_id': client_id,
    'client_secret': secret,
    'code': code,
    'redirect_uri': redirect
  }
  headers = {
    'User-Agent': 'Mozilla/4.0 (compatible; MSIE 5.5; Windows NT)',
    'Accept': 'application/json'
  }
  params = urllib.parse.urlencode(data).encode('utf-8')
  req = urllib.request.Request(url, params, headers)
  print(req)
  html = urllib.request.urlopen(req).read().decode('utf-8')
  
  token_info = {}
  lst = html.split('&')
  for sText in lst:
    subList = sText.split('=')
    if len(subList) != 2:
      continue
    key, value = subList
    token_info[key] = value
  
  print(token_info, html, 'token_info')  
  if not 'access_token' in token_info:
    return bad_request('链接已失效，请重新登录', True)
  access_token = token_info['access_token']
  req2 = urllib.request.Request(url="https://graph.qq.com/oauth2.0/me?access_token="+access_token, headers=headers)
  html2 = urllib.request.urlopen(req2).read().decode('utf-8')
  reg = re.compile('\{.*\}')
  info_str = reg.search(html2).group(0)
  info = json.loads(info_str)
  print(info, 'info')
  if not 'openid' in info:
    return bad_request('链接已失效，请重新登录', True)
  id_string = 'qq' + info['openid']
  user = User.query.filter_by(id_string=id_string).first()
  print('user', user)
  if not user:
    data = {
      'openid': info['openid'],
      'access_token': access_token,
      'oauth_consumer_key': client_id
    }
    params3 = urllib.parse.urlencode(data).encode('utf-8')
    url3 = 'https://graph.qq.com/user/get_user_info'
    req3 = urllib.request.Request(url = url3, data = params3, headers = headers)
    html3 = urllib.request.urlopen(req3).read().decode('utf-8')
    user_info = json.loads(html3)

    avatar = ''
    if user_info.get("figureurl_qq_2"):
      avatar = save_file(user_info["figureurl_qq_2"])
    if not avatar:
      avatar = save_file(user_info['figureurl_qq_1'])
    if not avatar:
      return server_error('获取QQ用户信息失败', True)
    register_info = {
      'username': user_info['nickname'],
      'id_string': 'qq' + info['openid'],
      'avatar': avatar
    }
    user = User(**register_info)
    try:

      db.session.add(user)
      db.session.commit()

      reciver = current_app.config['FLASK_ADMIN']
      send_email(reciver, '用户注册', mail_type = 2, username = user_info['nickname'])
    except Exception as e:
      print(e)
      db.session.rollback()
      response = jsonify({ 'error': 'create user error', 'message': '创建用户失败，请重新登录' })
      response.status_code = 500
      return response
  token = user.generate_auth_token(3600 * 24 * 30)
  return jsonify({ 'token': token })

@api.route('/get-user/<user_id>')
def get_user_info(user_id):
  user = User.query.get(user_id)
  if not user:
    return not_found('获取不到用户信息')
  return jsonify(user.to_json())

@api.route('/get-user-detail/<user_id>')
def get_user_detail(user_id):
  user = User.query.get(user_id)
  if not user:
    return not_found('获取不到用户信息')
  info = user.to_json()
  if g.current_user and (g.current_user == user or g.current_user.can(Permission.ADMIN)):
    comments = user.comments
    messages = user.messages
  else:
    comments = user.comments.filter_by(hide = False)
    messages = user.messages.filter_by(hide = False)
  comments = list(map(lambda c: c.to_json(), comments.all()))
  messages = list(map(lambda m: m.to_json(), messages.all()))
  likes = list(map(lambda l: l.to_json(), user.likes.all()))
  info['comments'] = comments
  info['messages'] = messages
  info['likes'] = likes
  return jsonify(info)

@api.route('/get-self/')
@login_required
def get_self_info():
  if not g.current_user:
    return bad_request('未找到用户信息')
  return jsonify(g.current_user.get_detail())

@api.route('/check-admin/')
def check_admin():
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
  post_count = Post.query.filter(and_(Post.hide == False, Post.type_id != post_type.id)).count()
  json['post_count'] = post_count
  # json['like_count'] = Like.query.count()
  # json['comment_count'] = Comment.query.filter_by(hide = False).count()
  # json['message_count'] = Message.query.filter_by(hide = False).count()

  return jsonify(json)

@api.route('/set-email/', methods=["POST"])
def set_email():
  if not g.current_user:
    return unauthorized('请登录后再进行操作', True)
  user_id = request.json.get('user_id')
  if not g.current_user.is_administrator() and g.current_user.id != user_id:
    return forbidden('非法操作', True)
  email = request.json.get('email')
  if not email:
    return bad_request('请填写正确的邮箱', True)
  if User.query.filter_by(email=email).first():
    return bad_request('该邮箱已被占用，如果您是该邮箱所有者，请联系管理员', True)
  user = User.query.get(user_id)
  if not user:
    return bad_request('未找到用户信息', True)
  user.email = email
  try:
    db.session.add(user)
    db.session.commit()
  except Exception as e:
    print(e)
    db.session.rollback()
    response = server_error('设置邮箱失败，请重试', True)
    return response
  return jsonify({ 'message': '设置邮箱成功', "notify": True })

@api.route('/search-user/', methods = ["POST"])
@permission_required(Permission.ADMIN)
def search_user():
  keyword = request.json.get('keyword', '')
  user_list = User.query.filter(User.username.like('%{}%'.format(keyword))).all()
  user_list = list(map(lambda x: x.to_json(), user_list))
  return jsonify({ 'list': user_list })
