from flask import request, jsonify, g, current_app

from . import api
from .errors import *
from ..models import Message, Permission, User, Role
from .. import db
from .decorators import login_required, permission_required
from sqlalchemy import or_
from ..email import send_email
from ..defines import NOTIFY
from ..socket import notify
import time
from ..rich_content import validate_body

@api.route('/get-messages/', methods=["POST"])
def get_messages():
  page = request.json.get('page', '')
  per_page = current_app.config['FLASK_BBS_PER_PAGE']
  if not page:
    return bad_request('参数错误')
  if g.current_user and g.current_user.is_administrator():
    hideCondition = True
  else:
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
  replies = msg.comments
  if not g.current_user or not g.current_user.is_administrator():
    replies = replies.filter(or_(Message.hide == False, Message.author == g.current_user))
  lst = [msg] + replies.all()
  msg_list = list(map(lambda msg: msg.to_json(), lst))
  return jsonify({ 'list': msg_list })

@api.route('/add-message/', methods = ['POST'])
@login_required
def add_message():
  start = time.time()
  params = {}
  data = request.get_json(silent=True)
  if not isinstance(data, dict):
    return bad_request('请求格式不正确', True)
  try:
    body = validate_body(data.get('body', ''))
  except ValueError as error:
    return bad_request(str(error), True)
  params['body'] = body
  params['author'] = g.current_user
  response_id = data.get('response_id')
  if response_id and (type(response_id) is not int or response_id < 1):
    return bad_request('回复对象不正确', True)
  response = None
  if response_id:
    response = Message.query.get(response_id)
    if (not response or
        (response.hide and response.author_id != g.current_user.id and not g.current_user.can(Permission.ADMIN))):
      return not_found('查询不到该留言', True)
    if response:
      params['response_id'] = response_id
      params['root_response_id'] = response.root_response_id or response_id

  from ..guest import limit_guest_post
  limited = limit_guest_post(g.current_user, body)
  if limited is not None:
    return limited
  params['hide'] = True
  if g.current_user and g.current_user.can(Permission.ADMIN):
    params['hide'] = False
  msg = Message(**params)
  db.session.add(msg)
  db.session.commit()
  from .stat import record_business_event
  record_business_event('message.replied' if response_id else 'message.created', {
    'message_id': msg.id,
    'root_message_id': msg.root_response_id,
  })

  # 给管理员发送邮件
  domain = current_app.config["DOMAIN"]
  root_id = params.get('root_response_id', '') or msg.id
  url = '{}/message/{}?newId={}'.format(domain, root_id, msg.id)
  reciver = current_app.config['FLASK_ADMIN']
  notify_data = {
    "url": url,
    'content': body,
    'username': g.current_user.username
  }

  if not g.current_user.is_administrator():
    role_list = [r for r in Role.query.all() if r.has_permission(Permission.ADMIN)]
    role = role_list and role_list[0]
    if role:
      user = role.users.first()
      if user:
        notify(user.id, { **notify_data, 'type': NOTIFY["MESSAGE"] })
    send_email(reciver, '新增留言', mail_type = NOTIFY['MESSAGE'], **notify_data)

  # 给被回复者推送邮件
  if response:
    user_id = response.author_id
    user = User.query.get(user_id)
    if user and user != g.current_user:
      notify_status = notify(user_id, { 'type': NOTIFY["MESSAGE_REPLY"], **notify_data })
      if not notify_status and user.email:
        send_email(user.email, '留言回复', mail_type = NOTIFY["MESSAGE_REPLY"], **notify_data)
  return jsonify(msg.to_json())


@api.route('/messages/<int:message_id>/', methods=['PUT'])
@login_required
def edit_message(message_id):
  msg = Message.query.get(message_id)
  if not msg or (msg.author_id != g.current_user.id and not g.current_user.can(Permission.ADMIN)):
    return not_found('查询不到该留言', True)
  try:
    data = request.get_json(silent=True)
    body = validate_body(data.get('body') if isinstance(data, dict) else None)
  except ValueError as error:
    return bad_request(str(error), True)
  from ..guest import limit_guest_post
  limited = limit_guest_post(g.current_user, body)
  if limited is not None:
    return limited
  msg.body = body
  msg.hide = not g.current_user.can(Permission.ADMIN)
  db.session.commit()
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
  from .stat import record_business_event
  record_business_event('message.deleted', {'message_id': msg.id, 'root_message_id': msg.root_response_id})
  rows = Message.query.filter(or_(Message.id == msg.id, Message.root_response_id == (msg.root_response_id or msg.id))).all()
  descendants = {msg.id}
  while True:
    more = {row.id for row in rows if row.response_id in descendants} - descendants
    if not more:
      break
    descendants.update(more)
  for row in rows:
    if row.id in descendants:
      db.session.delete(row)
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
  from .stat import record_business_event
  record_business_event('message.approved', {'message_id': msg.id, 'root_message_id': msg.root_response_id})
  db.session.add(msg)
  return jsonify({ 'message': '设置成功', 'notify': True })
