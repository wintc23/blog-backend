from flask import request, current_app, jsonify, g
from .. import db
from . import api
from ..models import Post, Comment, Permission, User, Role
from .errors import *
from .decorators import login_required, permission_required
from sqlalchemy import or_
from ..email import send_email
from ..defines import NOTIFY
from ..socket import notify
from ..rich_content import validate_body


def target_comments(post_id=None, digest_id=None):
  admin = g.current_user and g.current_user.can(Permission.ADMIN)
  if bool(post_id) == bool(digest_id):
    return None, None
  if digest_id:
    from .ai_digest import _public_query
    from ..digest_models import AiDigest
    target = AiDigest.query.get(digest_id) if admin else _public_query().filter_by(id=digest_id).first()
    return target, Comment.query.filter_by(digest_id=digest_id)
  target = Post.query.get(post_id)
  if target and not admin and (target.hide or target.type.special):
    target = None
  return target, Comment.query.filter_by(post_id=post_id)


def visible_comments(query):
  if not g.current_user or not g.current_user.can(Permission.ADMIN):
    query = query.filter(or_(Comment.hide == False, Comment.author == g.current_user))
  return [comment.to_json() for comment in query.order_by(Comment.timestamp, Comment.id).all()]


@api.route('/comments/')
def public_comments():
  try:
    post_id = int(request.args['post_id']) if request.args.get('post_id') else None
    digest_id = int(request.args['digest_id']) if request.args.get('digest_id') else None
  except (TypeError, ValueError):
    return bad_request('评论对象不正确', True)
  target, query = target_comments(post_id, digest_id)
  if target is None:
    return not_found('查询不到该内容', True)
  rows = visible_comments(query)
  response = jsonify({'comments': rows, 'comment_times': len(rows)})
  response.headers['Cache-Control'] = 'no-store'
  return response

@api.route('/add-comment/', methods = ['POST'])
@login_required
def add_comment():
  params = {}
  data = request.get_json(silent=True)
  if not isinstance(data, dict):
    return bad_request('请求格式不正确', True)
  try:
    body = validate_body(data.get('body', ''))
  except ValueError as error:
    return bad_request(str(error), True)
  post_id, digest_id = data.get('post_id'), data.get('digest_id')
  if any(value is not None and (type(value) is not int or value < 1) for value in (post_id, digest_id)):
    return bad_request('评论对象不正确', True)
  post, query = target_comments(post_id, digest_id)
  if post is None:
    return not_found('查询不到该内容', True)
  params['body'] = body
  params['post_id'] = post_id
  params['digest_id'] = digest_id
  params['author'] = g.current_user
  response_id = data.get('response_id')
  if response_id and (type(response_id) is not int or response_id < 1):
    return bad_request('回复对象不正确', True)
  if response_id:
    response = Comment.query.get(response_id)
    if (not response or response.post_id != post_id or response.digest_id != digest_id or
        (response.hide and response.author_id != g.current_user.id and not g.current_user.can(Permission.ADMIN))):
      return not_found('查询不到该评论', True)
    if response:
      params['response'] = response
  from ..guest import limit_guest_post
  limited = limit_guest_post(g.current_user, body)
  if limited is not None:
    return limited
  params['hide'] = True
  if g.current_user and g.current_user.can(Permission.ADMIN):
    params['hide'] = False
  comment = Comment(**params)
  db.session.add(comment)
  db.session.flush()
  from ..interaction_notifications import enqueue
  enqueue('comment:' + str(comment.id), g.current_user, '收到评论回复' if response_id else '收到评论', body, '/{}/{}?commentId={}'.format('ai-news' if digest_id else 'article', digest_id or post_id, comment.id))
  db.session.commit()
  from .stat import record_business_event
  record_business_event('comment.replied' if response_id else 'comment.created', {
    'comment_id': comment.id,
    'post_id': post_id, 'digest_id': digest_id,
  })
  comments = visible_comments(query)
  domain = current_app.config["DOMAIN"]
  url = '{}/{}/{}?commentId={}#comments'.format(domain, 'ai-news' if digest_id else 'article', digest_id or post_id, comment.id)
  # 给管理员推送消息、邮件
  notify_data = {
    'url': url,
    'post_title': post.title,
    'content': body,
    'username': g.current_user.username
  }
  if not g.current_user.is_administrator():
    role_list = [r for r in Role.query.all() if r.has_permission(Permission.ADMIN)]
    role = role_list and role_list[0]
    if role:
      user = role.users.first()
      if user:
        notify(user.id, { **notify_data, 'type': NOTIFY["COMMENT"] })
    # Owner email and lark notifications are delivered by the durable outbox.

  # 给被回复者推送消息、邮件
  if "response" in params:
    user_id = params['response'].author_id
    user = User.query.get(user_id)
    if user and user != g.current_user:
      notify_status = notify(user_id, { 'type': NOTIFY["COMMENT_REPLY"], **notify_data })
      if not notify_status and user.email and not user.is_administrator():
        send_email(user.email, '评论回复', mail_type = NOTIFY["COMMENT_REPLY"], **notify_data)

  return jsonify({ "comment_times": len(comments), 'comments': comments })


@api.route('/comments/<int:comment_id>/', methods=['PUT'])
@login_required
def edit_comment(comment_id):
  comment = Comment.query.get(comment_id)
  if not comment or (comment.author_id != g.current_user.id and not g.current_user.can(Permission.ADMIN)):
    return not_found('查询不到该评论', True)
  target, _ = target_comments(comment.post_id, comment.digest_id)
  if target is None:
    return not_found('查询不到该内容', True)
  try:
    data = request.get_json(silent=True)
    body = validate_body(data.get('body') if isinstance(data, dict) else None)
  except ValueError as error:
    return bad_request(str(error), True)
  from ..guest import limit_guest_post
  limited = limit_guest_post(g.current_user, body)
  if limited is not None:
    return limited
  comment.body = body
  comment.hide = not g.current_user.can(Permission.ADMIN)
  db.session.commit()
  return jsonify(comment.to_json())

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
def delete_comment (comment_id):
  if not comment_id:
    return not_found('未找到该评论', True)

  comment = Comment.query.get(comment_id)
  if not comment:
    return not_found('未找到该评论', True)
  from .stat import record_business_event
  record_business_event('comment.deleted', {'comment_id': comment.id, 'post_id': comment.post_id})
  # Delete the reply subtree too, so no hidden orphan content/assets remain.
  pending = [comment]
  seen = set()
  while pending:
    item = pending.pop()
    if item.id in seen:
      continue
    seen.add(item.id)
    pending.extend(item.comments.all())
    db.session.delete(item)
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
  from .stat import record_business_event
  record_business_event('comment.approved', {'comment_id': comment.id, 'post_id': comment.post_id})
  db.session.add(comment)
  return jsonify({ 'message': '设置成功', 'notify': True })
