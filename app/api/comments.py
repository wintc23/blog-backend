from flask import request, current_app, jsonify, g
from .. import db
from . import api
from ..models import Post, Comment, Permission
from .errors import *
from .decorators import login_required, permission_required

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
  post = Post.query.get(post_id)
  if not post:
    return bad_request('请求错误！', True)
  params['body'] = body
  params['post_id'] = post_id
  params['author'] = g.current_user
  response_id = request.json.get('response_id', '')
  if response_id:
    response = Comment.query.get(response_id)
    if response:
      params['response'] = response
  params['hide'] = True
  if g.current_user and g.current_user.can(Permission.ADMIN):
    params['hide'] = False
  comment = Comment(**params)
  db.session.add(comment)
  if g.current_user and g.current_user.can(Permission.ADMIN):
    comments = post.comments.all()
  else:
    hideCondition = or_(Comment.hide == False, Comment.author == g.current_user)
    comments = post.comments.filter(hideCondition).all()
  comments = list(map(lambda comment: comment.to_json(), comments))
  return jsonify({ "comment_times": len(comments), 'comments': comments })

@api.route('/get-comments/', methods = ['POST'])
@permission_required(Permission.ADMIN)
def get_comments ():
  page = request.json.get('page', 1)
  per_page = request.json.get('per_page', 10)
  pagination = Comment.query.order_by(Comment.timestamp.desc()).order_by(Comment.hide.desc()).paginate(
    page,
    per_page = per_page,
    error_out = False
  )
  comments = list(map(lambda comment: comment.to_json(), pagination.items))
  return jsonify({
    'list': comments,
    'total': pagination.total,
    'page': page
  })

@api.route('/delete-comment/<comment_id>')
@permission_required(Permission.ADMIN)
def deleteComment (comment_id):
  if not comment_id:
    return not_found('未找到该评论', True)
  comment = Comment.query.get(comment_id)
  if not comment:
    return not_found('未找到该评论', True)
  db.session.delete(comment)
  return jsonify({ 'message': '删除评论成功', 'notify': True })

@api.route('/set-comment-show/<comment_id>')
@permission_required(Permission.ADMIN)
def set_comment_show(comment_id):
  if not comment_id:
    return bad_request('参数错误', True)
  comment = Comment.query.get(comment_id)
  if not comment:
    return not_found('未找到该评论', True)
  comment.hide = False
  db.session.add(comment)
  return jsonify({ 'message': '设置成功', 'notify': True })
