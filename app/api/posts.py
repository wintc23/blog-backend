
from flask import request, current_app, jsonify, g
from .. import db
from . import api
from ..models import PostType, Post, Permission
from .errors import *
from .decorators import *


@api.route('/get-post-type/')
def get_post_types():
  type_list = PostType.query.all()
  return jsonify({ 'list': list(map(lambda t: t.to_json(), type_list)) })

@api.route('/get-posts/', methods=["POST"])
def get_posts():
  post_type_id = request.json.get('post_type', '')
  post_type = None
  if post_type_id:
    post_type = PostType.query.get(post_type_id)
  if not post_type:
    post_type = PostType.query.filter_by(default = True).first()
  if not post_type:
    response = server_error('服务器查询数据库失败', True)
    return response
  page = request.json.get('page', 1)
  per_page = request.json.get('per_page', 5)
  if g.current_user and g.current_user.can(Permission.ADMIN):
    query = post_type.posts
  else:
    query = post_type.posts.filter_by(hide = False)
  pagination = query.order_by(Post.timestamp.desc()).paginate(
    page,
    per_page = per_page,
    error_out = False
  )
  post_list = list(map(lambda post: post.abstract_json(), pagination.items))
  types = PostType.query.all()
  type_list = list(map(lambda t: t.to_json(), types))
  return jsonify({
    'list': post_list,
    'type_list': type_list,
    'total': pagination.total,
    'page': page
  })

@api.route('/get-post/<post_id>')
def get_post(post_id):
  post = Post.query.get(post_id)
  if not post:
    return not_found('查询不到该文章', True)
  json = post.to_json()
  post_type = post.type
  condition = {}
  if g.current_user and g.current_user.can(Permission.ADMIN):
    post_list = post_type.posts.filter().all()
  else:
    post_list = post_type.posts.filter(hide = False).all()
    if not post in post_list:
      return not_found('查询不到该文章', True)
  index = post_list.index(post)
  before = None
  after = None
  length = len(post_list)
  if length > index + 1:
    after = post_list[index + 1].abstract_json()
  if index > 0:
    before = post_list[index - 1].abstract_json()
  comments = post.comments.all()
  json['before'] = before
  json['after'] = after
  json['comments'] = list(map(lambda comment: comment.to_json(), comments))
  return jsonify(json)

@api.route('/add-post/<post_type_id>')
@permission_required(Permission.WRITE)
def add_post(post_type_id):
  post = Post(author = g.current_user, hide = True, type_id = post_type_id)
  db.session.add(post)
  db.session.commit()
  print(post.id)
  return jsonify({ 'message': '创建文章成功', 'post_id': post.id, 'notify': True })

@api.route('/save-post/', methods = ['POST'])
@permission_required(Permission.WRITE)
def save_post():
  post_id = request.json.get('id')
  if not post_id:
    return not_found('查找不到文章', True)
  post = Post.query.get(post_id)
  if not post:
    return not_found('查找不到文章', True)
  for key in request.json:
    setattr(post, key, request.json[key])
  db.session.add(post)
  return jsonify({
    'message': '保存成功',
    'notify': True
  })

@api.route('/delete-post/<post_id>')
@permission_required(Permission.ADMIN)
def delete_post(post_id):
  post = Post.query.get(post_id)
  if not post:
    return not_found('查找不到文章', True)
  db.session.delete(post)
  return jsonify({
    'message': '删除成功',
    'notify': True
  })
