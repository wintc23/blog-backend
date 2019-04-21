
from flask import request, current_app, jsonify, g
from .. import db
from . import api
from ..models import PostType, Post, Permission, Like, Comment
from .errors import *
from .decorators import *
from sqlalchemy import or_

@api.route('/get-post-type/')
def get_post_types():
  if g.current_user and g.current_user.can(Permission.ADMIN):
    type_list = PostType.query.filter_by().all()
  else:
    type_list = PostType.query.filter_by(special = 0).all()
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
  query = post_type.posts
  if not g.current_user or not g.current_user.can(Permission.ADMIN):
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
    post_list = post_type.posts.all()
  else:
    post_list = post_type.posts.filter_by(hide = False).all()
    if not post in post_list:
      return not_found('查询不到该文章', True)
  json['like'] = False
  if g.current_user:
    if post.likes.filter_by(author = g.current_user).first():
      json['like'] = True
  index = post_list.index(post)
  before = None
  after = None
  length = len(post_list)
  if length > index + 1:
    after = post_list[index + 1].abstract_json()
  if index > 0:
    before = post_list[index - 1].abstract_json()
  if g.current_user and g.current_user.can(Permission.ADMIN):
    comments = post.comments.all()
  else:
    hideCondition = or_(Comment.hide == False, Comment.author == g.current_user)
    comments = post.comments.filter(hideCondition).all()
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

@api.route('/get-about-me/')
def get_about_me():
  post_type = PostType.query.filter_by(special = 1).first()
  post = post_type.posts.first()
  if not g.current_user or not g.current_user.can(Permission.ADMIN):
    if post.hide:
      return not_found('获取数据失败', True)
  return jsonify(post.to_json())

@api.route('/like-post/<post_id>')
@login_required
def like_post(post_id):
  if not post_id:
    return not_found('未找到文章')
  post = Post.query.get(post_id)
  if not post:
    return not_found('找不到文章')
  like = post.likes.filter_by(author = g.current_user).first()
  if like:
    return bad_request('您赞过该文章了')
  like = Like(post_id = post_id, author = g.current_user)
  db.session.add(like)
  return jsonify({'message': '您点赞了该文章', 'notify': True})

@api.route('/cancel-like-post/<post_id>')
@login_required
def cancel_like_post(post_id):
  if not post_id:
    return not_found('未找到文章')
  post = Post.query.get(post_id)
  if not post:
    return not_found('找不到文章')
  like = post.likes.filter_by(author = g.current_user).first()
  if not like:
    return not_found('未曾点赞')
  db.session.delete(like)
  return jsonify({ 'message': '您取消了点赞', 'notify': True })
