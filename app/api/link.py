from flask import request, jsonify, g, current_app
from .. import db

from . import api
from .errors import *
from ..models import FriendLink, Permission
from ..email import send_email
from sqlalchemy import or_
from .decorators import *

@api.route('/get-link-list/', methods = ["GET"])
def get_link_list():
  hideCondition = FriendLink.hide == False
  if g.current_user:
    if g.current_user.is_administrator():
      hideCondition = True
    else:
      hideCondition = or_(FriendLink.hide == False, FriendLink.author_id == g.current_user.id)
  link_list = FriendLink.query.filter(hideCondition).all()
  link_list = list(map(lambda x: x.to_json(), link_list))
  return jsonify({ 'list': link_list })

@api.route('/get-basic-link-list/', methods = ["GET"])
def get_base_link_list():
  link_list = FriendLink.query.filter(FriendLink.hide == False).all()
  link_list = list(map(lambda x: x.to_abstract(), link_list))
  return jsonify({ 'list': link_list })

@api.route('/add-link/', methods = ["POST"])
@login_required
def add_link():
  params = {}
  params['title'] = request.json.get('title', '')
  if not params['title']:
    return bad_request('标题不能为空', True)
  params['link'] = request.json.get('link', '')
  if not params['link']:
    return bad_request('站点地址不能为空', True)
  params['motto'] = request.json.get('motto', '')
  params['logo'] = request.json.get('logo', '')
  params['hide'] = True
  if g.current_user.is_administrator():
    params['hide'] = False
  else:
    params['author_id'] = g.current_user.id
  link = FriendLink(**params)
  db.session.add(link)
  db.session.commit()
  domain = current_app.config["DOMAIN"]

  return jsonify({ 'message': '添加成功', 'notify': True })

@api.route('/delete-link/<link_id>', methods = ['GET'])
@permission_required(Permission.ADMIN)
def delete_link(link_id):
  link = FriendLink.query.get(link_id)
  if not link:
    return bad_request('找不到友链', True)
  db.session.delete(link)
  return jsonify({ 'message': '删除成功', 'notify': True })

@api.route('/check-link/<link_id>', methods = ["GET"])
@permission_required(Permission.ADMIN)
def check_link(link_id):
  link = FriendLink.query.get(link_id)
  if not link:
    return bad_request('找不到友链', True)
  link.hide = False
  db.session.add(link)

  return jsonify({ 'message': '操作成功', 'notify': True })

@api.route('/update-link/', methods = ["POST"])
@login_required
def update_link():
  link_id = request.json.get('id', None)
  if not link_id:
    return bad_request("更新失败，友链不存在", True)
  link = FriendLink.query.get(link_id)
  if not link:
    return bad_request("更新失败，友链不存在", True)
  if not g.current_user.is_administrator() and g.current_user.id != link.author_id:
    return bad_request('无权限更新', True)
  link.title = request.json.get('title', link.title)
  link.link = request.json.get('link', link.link)
  link.logo = request.json.get('logo', link.logo)
  link.motto = request.json.get('motto', link.motto)
  link.hide = True
  if g.current_user.is_administrator():
    link.hide = False
    link.author_id = request.json.get('author_id', link.author_id)
  db.session.add(link)
  db.session.commit()

  return jsonify({ 'message': '操作成功', 'notify': True })