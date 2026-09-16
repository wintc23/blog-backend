"""Read published digests and let administrators preview persisted drafts."""
import json
from datetime import datetime, timedelta
from flask import g, jsonify, request
from . import api
from .decorators import permission_required
from .errors import bad_request, forbidden, not_found, unauthorized
from .. import db
from ..models import Permission
from ..digest_models import AiDigest, AiDigestSettings, AiNewsItem, AiNewsSource


def _admin():
    return bool(g.current_user and g.current_user.can(Permission.ADMIN))


def _iso(value):
    return value.isoformat() + 'Z' if value else None


def _utcnow():
    return datetime.utcnow()


def _public_query(now=None):
    now = now or _utcnow()
    today = (now + timedelta(hours=8)).date()
    return AiDigest.query.filter(
        AiDigest.status == 'published', AiDigest.published_at.isnot(None),
        AiDigest.published_at <= now, AiDigest.scheduled_publish_at <= now,
        AiDigest.issue_date <= today)


def _settings():
    settings = AiDigestSettings.query.get(1)
    return {'title': settings.title, 'timezone': settings.timezone,
            'publish_time': settings.publish_time.strftime('%H:%M')} if settings else None


def serialize_digest(digest, full=False, channel_title=None):
    content = json.loads(digest.content_json)
    if channel_title is None:
        settings = _settings()
        channel_title = settings['title'] if settings else 'AI 行业动态'
    result = {
        'id': digest.id, 'issue_date': digest.issue_date.isoformat(),
        'title': digest.title, 'slug': digest.slug, 'summary': digest.summary,
        'status': digest.status, 'content_version': digest.content_version,
        'read_times': digest.read_times,
        'channel_title': channel_title,
        'cover': content.get('cover'),
        'groups': [dict(group, count=sum(1 for block in content.get('sections', []) if block.get('group_id') == group['id']))
                   for group in content.get('groups', [])
                   if any(block.get('group_id') == group['id'] for block in content.get('sections', []))],
        'timezone': digest.timezone,
        'scheduled_publish_at': _iso(digest.scheduled_publish_at),
        'published_at': _iso(digest.published_at),
        'created_at': _iso(digest.created_at), 'updated_at': _iso(digest.updated_at),
    }
    if full:
        # Keep the public v1 base contract readable during frontend rollouts.
        # Group metadata is additive; the stored document remains schema v2.
        result['content'] = dict(content, schema_version=1) if content.get('schema_version') == 2 else content
        result['source_window_start'] = _iso(digest.source_window_start)
        result['source_window_end'] = _iso(digest.source_window_end)
    return result


@api.route('/ai-digests/')
def get_ai_digests():
    manage = request.args.get('manage') == '1'
    if manage and not _admin():
        return forbidden('需要管理员权限') if g.current_user else unauthorized('请先登录')
    try:
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 10))
    except (ValueError, TypeError):
        return bad_request('分页参数不正确')
    if page < 1 or not 1 <= per_page <= 30:
        return bad_request('分页参数不正确')
    query = AiDigest.query
    if not manage:
        query = _public_query()
    result = query.order_by(AiDigest.issue_date.desc()).paginate(page, per_page=per_page, error_out=False)
    settings = _settings()
    title = settings['title'] if settings else 'AI 行业动态'
    return jsonify({'list': [serialize_digest(row, channel_title=title) for row in result.items],
                    'page': page, 'per_page': per_page, 'total': result.total,
                    'settings': settings})


@api.route('/ai-digests/home/')
def get_ai_digest_home():
    now = _utcnow()
    today = (now + timedelta(hours=8)).date()
    rows = _public_query(now).order_by(AiDigest.issue_date.desc()).limit(6).all()
    latest = rows[0] if rows else None
    settings = _settings()
    title = settings['title'] if settings else 'AI 行业动态'
    return jsonify({'featured': serialize_digest(latest, channel_title=title) if latest else None,
                    'previous': [serialize_digest(row, channel_title=title) for row in rows[1:]],
                    'is_today': bool(latest and latest.issue_date == today),
                    'today': today.isoformat(), 'settings': settings})


@api.route('/ai-digests/<int:digest_id>/')
def get_ai_digest(digest_id):
    digest = (AiDigest.query.get(digest_id) if _admin()
              else _public_query().filter_by(id=digest_id).first())
    if not digest:
        return not_found('动态不存在')
    return jsonify(serialize_digest(digest, full=True))


@api.route('/ai-digests/<int:digest_id>/read/', methods=['POST'])
def record_ai_digest_read(digest_id):
    # GET requests (SSR, metadata, prefetch, previews) never change analytics.
    query = _public_query().filter(AiDigest.id == digest_id)
    digest = query.first()
    if not digest:
        return not_found('动态不存在')
    # An expired/unrecognised credential must not count an owner as anonymous.
    if _admin() or (request.headers.get('Authorization') and not g.current_user):
        return jsonify({'read_times': digest.read_times, 'counted': False})
    # Increment in SQL so concurrent visits cannot overwrite one another.
    if not query.update({AiDigest.read_times: AiDigest.read_times + 1}, synchronize_session=False):
        return not_found('动态不存在')
    db.session.commit()
    return jsonify({'read_times': AiDigest.query.get(digest_id).read_times, 'counted': True})


@api.route('/ai-news-items/')
@permission_required(Permission.ADMIN)
def get_ai_news_items():
    rows = (AiNewsItem.query.join(AiNewsSource, AiNewsItem.source_id == AiNewsSource.id)
            .add_entity(AiNewsSource).order_by(AiNewsItem.published_date.desc(), AiNewsItem.id.desc()).limit(100).all())
    return jsonify({'list': [{
        'id': item.id, 'title': item.title, 'url': item.canonical_url,
        'source_name': source.name,
        'published_date': item.published_date.isoformat() if item.published_date else None,
        'first_seen_at': _iso(item.first_seen_at),
        'evidence': json.loads(item.evidence_json),
    } for item, source in rows]})
