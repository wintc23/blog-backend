from flask import request, jsonify, g

from . import api
from .errors import *
from ..models import Message
from .. import db
from .decorators import login_required

@api.route('/get-messages/', methods=["POST"])
def get_messages():
  page = request.json.get('page', '')
  per_page = request.json.get('per_page', '')
  if not page or not per_page:
    return bad_request('参数错误')
  pagination = Message.query.filter_by(response_id = None).order_by(Message.timestamp.desc()).paginate(
    page,
    per_page = per_page,
    error_out = False
  )
  msgs = pagination.items
  total = pagination.total

  lst = []
  for msg in msgs:
    lst.append(msg)
    lst += msg.comments
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
  msg = Message(**params)
  db.session.add(msg)
  return jsonify({ 'message': '留言成功', 'notify': True })

