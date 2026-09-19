"""Public content reactions share the existing guest-login and like limits."""
import hashlib
import json
from datetime import datetime
from flask import g, jsonify, request
from sqlalchemy.exc import IntegrityError
from . import api
from .image_tools import endpoint, ToolError
from .. import db
from ..models import LifeMoment, StatEvent
from ..content_like_models import ContentLike
from ..image_tool_models import ImageTool, ImageTask
from ..guest import consume_limits


def react(kind, target_id):
    user = g.current_user
    if not user and (request.method != 'GET' or request.headers.get('Authorization')):
        raise ToolError('请先登录', 401)
    query = ContentLike.query.filter_by(kind=kind, target_id=target_id)
    own = query.filter_by(author_id=user.id) if user else None
    if request.method == 'POST' and not own.first():
        if user.is_guest:
            limited = consume_limits(str(user.id), [('like-minute', 5, 60), ('like-hour', 30, 3600)])
            if limited is not None: return limited
        db.session.add(ContentLike(kind=kind, target_id=target_id, author_id=user.id))
        db.session.add(StatEvent(name='content.like', author_id=user.id, params=json.dumps({'target': kind})))
        try: db.session.commit()
        except IntegrityError:
            db.session.rollback()
            if not own.first(): raise
    elif request.method == 'DELETE':
        own.delete(synchronize_session=False); db.session.commit()
    return jsonify(likes=query.count(), like=bool(own is not None and own.first()))


@api.route('/life-moments/<moment_id>/likes/', methods=['GET', 'POST', 'DELETE'])
@endpoint
def moment_likes(moment_id):
    if not LifeMoment.query.get(moment_id): raise ToolError('动态不存在', 404)
    return react('moment', moment_id)


@api.route('/image-tools/<slug>/likes/', methods=['GET', 'POST', 'DELETE'])
@endpoint
def tool_likes(slug):
    if not ImageTool.query.filter_by(slug=slug, enabled=True).first(): raise ToolError('工具不存在', 404)
    return react('tool', slug)


@api.route('/image-shares/<token>/likes/', methods=['GET', 'POST', 'DELETE'])
@endpoint
def share_likes(token):
    digest = hashlib.sha256(token.encode()).hexdigest()
    task = ImageTask.query.filter_by(share_hash=digest, deleted_at=None).first()
    if not task or not task.share_until or task.share_until <= datetime.utcnow():
        raise ToolError('分享已过期或被取消', 404)
    return react('image_share', digest)
