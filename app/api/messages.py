from flask import request, jsonify, g

from . import api
from .errors import *
from ..models import Message, Permission
from .. import db
from .decorators import login_required, permission_required
from sqlalchemy import or_

@api.route('/get-messages/', methods=["POST"])
def get_messages():
  page = request.json.get('page', '')
  per_page = request.json.get('per_page', '')
  if not page or not per_page:
    return bad_request('参数错误')
  hideCondition = or_(Message.hide == False, Message.author == g.current_user)
  pagination = Message.query.filter_by(response_id = None).filter(hideCondition).order_by(Message.timestamp.desc()).paginate(
    page,
    per_page = per_page,
    error_out = False
  )
  msgs = pagination.items
  total = pagination.total

  lst = []
  for msg in msgs:
    lst.append(msg)
    lst += msg.comments.filter(hideCondition).all()
  msg_list = list(map(lambda msg: msg.to_json(), lst))
  return jsonify({
    'list': msg_list,
    'total': total,
    'page': page
  })

@api.route('/add-message/', methods = ['POST'])
@login_required
def add_message():
  params = {}
  body = request.json.get('body', '')
  if not body:
    return bad_request('评论内容不能为空', True)
  params['body'] = body
  params['author'] = g.current_user
  response_id = request.json.get('response_id', '')
  if (response_id):
    response = Message.query.get(response_id)
    if (response):
      params['response_id'] = response_id
      params['root_response_id'] = response.root_response_id or response_id
  params['hide'] = True
  if g.current_user and g.current_user.can(Permission.ADMIN):
    params['hide'] = False
  msg = Message(**params)
  db.session.add(msg)
  return jsonify({ 'message': '留言成功（留言审核通过才会公开）', 'notify': True })

@api.route('/get-hide-messages/', methods = ['POST'])
@permission_required(Permission.ADMIN)
def get_hide_messages():
  page = request.json.get('page', 1)
  per_page = request.json.get('per_page', 10)
  pagination = Message.query.order_by(Message.timestamp.desc()).order_by(Message.hide.desc()).paginate(
    page,
    per_page = per_page,
    error_out = False
  )
  msgs = list(map(lambda msg: msg.to_json(), pagination.items))
  return jsonify({
    'list': msgs,
    'total': pagination.total,
    'page': page
  })

@api.route('/delete-message/<msg_id>')
@permission_required(Permission.ADMIN)
def deleteMessage(msg_id):
  if not msg_id:
    return not_found('未找到该留言', True)
  msg = Message.query.get(msg_id)
  if not msg:
    return not_found('未找到该留言', True)
  db.session.delete(msg)
  return ({ 'message': '留言删除成功', 'notify': True })

@api.route('/set-message-show/<msg_id>')
@permission_required(Permission.ADMIN)
def set_message_show(msg_id):
  if not msg_id:
    return bad_request('参数错误', True)
  msg = Message.query.get(msg_id)
  if not msg:
    return not_found('未找到该留言', True)
  msg.hide = False
  db.session.add(msg)
  return jsonify({ 'message': '设置成功', 'notify': True })

