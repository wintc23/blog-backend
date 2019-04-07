from flask import request, current_app, jsonify, g
from .. import db
from . import api
from ..models import Post, Comment
from .errors import *
from .decorators import login_required

@api.route('/add-comment/', methods = ['POST'])
@login_required
def add_comment():
  params = {}
  body = request.json.get('body', '')
  if not body:
    return bad_request('评论内容不能为空', True)
  post_id = request.json.get('post_id', '')
  if not post_id:
    return bad_request('请求错误！', True)
  params['body'] = body
  params['post_id'] = post_id
  params['author'] = g.current_user
  response_id = request.json.get('response_id', '')
  if response_id:
    response = Comment.query.get(response_id)
    if response:
      params['response'] = response
  comment = Comment(**params)
  db.session.add(comment)
  return jsonify({ 'message': '评论成功', 'notify': True })