from flask import request, jsonify, g, current_app

from . import api
from .errors import *
from ..models import Message, Permission, User
from .. import db
from .decorators import login_required, permission_required
from sqlalchemy import or_
from ..email import send_email

@api.route('/get-messages/', methods=["POST"])
def get_messages():
  page = request.json.get('page', '')
  per_page = current_app.config['FLASK_BBS_PER_PAGE']
  if not page:
    return bad_request('参数错误')
  hideCondition = Message.hide == False
  if g.current_user:
    hideCondition = or_(Message.hide == False, Message.author_id == g.current_user.id)
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
    'page': page,
    'per_page': per_page
  })

@api.route('/get-message-detail/<msg_id>')
def get_message_detail(msg_id):
  msg = Message.query.get(msg_id)
  if not msg:
    return not_found('没有找到该留言', True)
  show = True
  if msg.hide:
    show = False
    if g.current_user:
      show = g.current_user.is_administrator() or g.current_user.id == msg.author_id
  if not show:
    return not_found('没有找到该留言', True)
  lst = [msg] + msg.comments.all()
  msg_list = list(map(lambda msg: msg.to_json(), lst))
  return jsonify({ 'list': msg_list })

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
  response = None
  if response_id:
    response = Message.query.get(response_id)
    if response:
      params['response_id'] = response_id
      params['root_response_id'] = response.root_response_id or response_id

  params['hide'] = True
  if g.current_user and g.current_user.can(Permission.ADMIN):
    params['hide'] = False
  msg = Message(**params)
  db.session.add(msg)
  db.session.commit()

  # 给管理员发送邮件
  domain = current_app.config["DOMAIN"]
  url = '{}/message/{}'.format(domain, params.get('root_response_id', '') or msg.id)
  reciver = current_app.config['FLASK_ADMIN']
  send_email(reciver,
    '新增留言',
    mail_type = 6,
    url = url,
    user = g.current_user,
    content = body)
  # 给被回复者推送邮件
  if response:
    user_id = response.author_id
    user = User.query.get(user_id)
    if user and user != g.current_user and user.email:
      send_email(user.email,
      '留言回复',
      mail_type = 7,
      url = url,
      user = g.current_user,
      content = body)
  return jsonify(msg.to_json())

@api.route('/get-hide-messages/', methods = ['POST'])
@permission_required(Permission.ADMIN)
def get_hide_messages():
  page = request.json.get('page', 1)
  per_page = current_app.config['FLASK_BBS_PER_PAGE']
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
def delete_message(msg_id):
  if not msg_id:
    return not_found('未找到该留言', True)
  msg = Message.query.get(msg_id)
  if not msg:
    return not_found('未找到该留言', True)
  db.session.delete(msg)
  return jsonify({ 'message': '留言删除成功', 'notify': True })

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

