"""Administrator-only generation control and content management."""
import copy
import hashlib
import json
from datetime import date, timedelta
from functools import wraps
from flask import jsonify, request
from sqlalchemy.exc import IntegrityError
from . import api
from .decorators import permission_required
from .errors import bad_request, not_found
from .. import db
from ..models import Permission
from ..digest_models import AiNewsSource, AiDigest, AiDigestSettings
from ..generation_models import (GenerationTask as Task, GenerationTaskVersion as Version,
    GenerationJob as Job, GenerationRun as Run, GeneratedContent as Content, ContentRevision as Revision,
    GenerationHeartbeat)
from ..generation.configuration import default_config, validate_config, encode, iso, utcnow, local_day, schedule_for, GenerationError
from ..generation import engine, providers
from ..generation.digest import ADAPTERS
from ..generation.network import validate_url
from ..generation.sources import read_feed


def admin(func):
    @permission_required(Permission.ADMIN)
    @wraps(func)
    def handler(*args, **kwargs):
        if request.content_length and request.content_length > 300000:
            return bad_request('提交内容过大')
        try:
            return func(*args, **kwargs)
        except (ValueError, TypeError, KeyError, GenerationError) as exc:
            db.session.rollback()
            message = str(exc) if isinstance(exc, (ValueError, GenerationError)) else '提交字段不完整或格式错误'
            return bad_request(message)
        except IntegrityError:
            db.session.rollback()
            return jsonify({'message': '记录已存在或被其他操作更新，请刷新重试'}), 409
    return handler


def body():
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise ValueError('请提交 JSON 对象')
    return value


def task_json(task):
    version = engine.task_version(task)
    config = validate_config(json.loads(version.config_json))
    now = utcnow()
    edition = local_day(now, config)
    generate, publish, deadline = schedule_for(edition, config)
    if now > generate:
        generate, publish, _ = schedule_for(edition + timedelta(days=1), config)
    last_job = Job.query.filter_by(task_id=task.id).order_by(Job.id.desc()).first()
    return {'id': task.id, 'name': task.name, 'content_type': task.content_type, 'channel': task.channel,
            'environment': task.environment, 'enabled': task.enabled, 'version': task.version, 'config': config,
            'next_generate_at': iso(generate) if task.enabled else None, 'next_publish_at': iso(publish) if task.enabled else None,
            'missing_configuration': providers.readiness(config) + ([] if config['source_ids'] else ['source_ids']), 'last_job': job_json(last_job) if last_job else None}


def job_json(job, full=False):
    value = {k: getattr(job, k) for k in ('id', 'task_id', 'version_id', 'environment', 'purpose', 'status', 'stage', 'attempt', 'error_code', 'error_message')}
    value.update(edition=job.edition.isoformat(), next_attempt_at=iso(job.next_attempt_at), deadline_at=iso(job.deadline_at),
                 created_at=iso(job.created_at), updated_at=iso(job.updated_at))
    if full:
        value['checkpoint'] = json.loads(job.checkpoint_json)
        value['runs'] = [{'id': r.id, 'attempt': r.attempt, 'status': r.status, 'stage': r.stage,
            'error_code': r.error_code, 'error_message': r.error_message, 'result': json.loads(r.result_json),
            'started_at': iso(r.started_at), 'finished_at': iso(r.finished_at)} for r in Run.query.filter_by(job_id=job.id).order_by(Run.id).all()]
    return value


def content_json(content, full=False):
    revision = Revision.query.filter_by(content_id=content.id, revision=content.current_revision).one()
    doc = json.loads(revision.document_json)
    settings = AiDigestSettings.query.get(1)
    digest = AiDigest.query.get(content.legacy_digest_id) if content.legacy_digest_id else None
    result = {'id': content.id, 'title': doc['title'], 'summary': doc['summary'], 'channel': content.channel,
        'channel_title': settings.title if settings else ADAPTERS[content.content_type]['label'],
        'created_at': iso(content.created_at), 'published_at': iso(digest.published_at) if digest else None,
        'read_times': digest.read_times if digest else 0,
        'content_type': content.content_type, 'edition': content.edition.isoformat(), 'status': content.status,
        'current_revision': content.current_revision, 'published_revision': content.published_revision,
        'legacy_digest_id': content.legacy_digest_id, 'scheduled_publish_at': iso(content.scheduled_publish_at),
        'updated_at': iso(content.updated_at), 'cover': doc['content'].get('cover'), 'validated': revision.validated}
    if full:
        result['document'] = doc
        result['revisions'] = [{'revision': r.revision, 'origin': r.origin, 'run_id': r.run_id,
            'job_id': Run.query.get(r.run_id).job_id if r.run_id else None,
            'created_at': iso(r.created_at), 'validated': r.validated} for r in Revision.query.filter_by(content_id=content.id).order_by(Revision.revision.desc()).all()]
    return result


@api.route('/generation/meta/')
@admin
def generation_meta():
    now = utcnow()
    heartbeats = GenerationHeartbeat.query.filter(GenerationHeartbeat.last_seen_at > now - timedelta(days=1)).all()
    return jsonify({'today': local_day(now, default_config()).isoformat(), 'default_config': default_config(), 'content_types': [dict(value=k, label=v['label'], channel=v['channel']) for k, v in ADAPTERS.items()],
        'heartbeats': [{'role': h.role, 'environment': h.environment, 'last_seen_at': iso(h.last_seen_at),
                       'healthy': h.last_seen_at > now - timedelta(seconds=60)} for h in heartbeats]})


@api.route('/generation/tasks/', methods=['GET', 'POST'])
@admin
def generation_tasks():
    if request.method == 'GET':
        return jsonify({'list': [task_json(t) for t in Task.query.order_by(Task.id).all()]})
    data = body()
    kind = data.get('content_type')
    if kind not in ADAPTERS or data.get('environment') not in ('production', 'development'):
        raise ValueError('内容类型或执行环境不正确')
    name = data.get('name', '').strip()
    if not name or len(name) > 128:
        raise ValueError('请填写任务名称，最多 128 字')
    config = validate_config(data.get('config', {}))
    task = Task(name=name, content_type=kind, channel=ADAPTERS[kind]['channel'], environment=data['environment'], enabled=False)
    db.session.add(task)
    db.session.flush()
    db.session.add(Version(task_id=task.id, version=task.version, config_json=encode(config)))
    db.session.commit()
    return jsonify(task_json(task)), 201


@api.route('/generation/tasks/<int:task_id>/', methods=['PUT'])
@admin
def update_generation_task(task_id):
    data = body()
    task = Task.query.filter_by(id=task_id).with_for_update().one_or_none()
    if not task:
        return not_found('任务不存在')
    if data.get('version') != task.version:
        return jsonify({'message': '配置已更新，请刷新后保存'}), 409
    config = validate_config(data['config'])
    if task.environment == 'development' and config['auto_publish']:
        raise ValueError('开发环境只生成草稿，不能自动发布')
    enabled = data.get('enabled', task.enabled)
    if type(enabled) is not bool:
        raise ValueError('启用状态不正确')
    if enabled:
        missing = providers.readiness(config)
        if missing or not config['source_ids']:
            raise ValueError('启用前请配置来源、模型和服务端凭据；缺少：' + ', '.join(missing))
    valid_ids = {s.id for s in AiNewsSource.query.filter(AiNewsSource.id.in_(config['source_ids']), AiNewsSource.kind == 'rss').all()}
    if valid_ids != set(config['source_ids']):
        raise ValueError('任务只能使用已保存的 RSS / Atom 来源')
    name = data.get('name', task.name)
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 128:
        raise ValueError('任务名称不正确')
    task.name, task.enabled, task.version, task.updated_at = name.strip(), enabled, task.version + 1, utcnow()
    db.session.add(Version(task_id=task.id, version=task.version, config_json=encode(config)))
    if not enabled:
        ids = [j.id for j in Job.query.filter(Job.task_id == task.id, Job.purpose == 'scheduled', Job.status.in_(['queued', 'running', 'retry_wait'])).all()]
        if ids:
            Job.query.filter(Job.id.in_(ids)).update({'status': 'cancelled', 'lock_token': None, 'lease_until': None, 'updated_at': utcnow()}, synchronize_session=False)
            Run.query.filter(Run.job_id.in_(ids), Run.status == 'running').update({'status': 'cancelled', 'finished_at': utcnow()}, synchronize_session=False)
    db.session.commit()
    return jsonify(task_json(task))


@api.route('/generation/tasks/<int:task_id>/run/', methods=['POST'])
@admin
def run_generation_task(task_id):
    task = Task.query.get(task_id)
    if not task:
        return not_found('任务不存在')
    data = body()
    config = json.loads(engine.task_version(task).config_json)
    purpose = data.get('purpose', 'test')
    if purpose not in ('test', 'regenerate'):
        raise ValueError('手动运行仅支持试运行或重新生成')
    edition = date.fromisoformat(data['edition'])
    if edition > local_day(utcnow(), config):
        raise ValueError('不能生成未来期次')
    reuse_cover = data.get('reuse_cover', False)
    if type(reuse_cover) is not bool or reuse_cover and purpose != 'regenerate':
        raise ValueError('只有重生成历史期次时可以复用封面')
    if providers.readiness(config, reuse_cover=reuse_cover) or purpose != 'regenerate' and not config['source_ids']:
        raise ValueError('请先保存完整的来源与模型配置，并设置服务端凭据')
    return jsonify(job_json(engine.enqueue(task, edition, purpose, data.get('request_id'), reuse_cover=reuse_cover))), 202


@api.route('/generation/jobs/')
@admin
def generation_jobs():
    page = max(1, int(request.args.get('page', 1)))
    rows = Job.query.order_by(Job.id.desc()).paginate(page, per_page=20, error_out=False)
    return jsonify({'list': [job_json(j) for j in rows.items], 'total': rows.total, 'page': page})


@api.route('/generation/jobs/<int:job_id>/', methods=['GET', 'POST'])
@admin
def generation_job(job_id):
    job = Job.query.filter_by(id=job_id).with_for_update().one_or_none() if request.method == 'POST' else Job.query.get(job_id)
    if not job:
        return not_found('执行任务不存在')
    if request.method == 'POST':
        job = engine.control_job(job.id, body().get('action'))
    return jsonify(job_json(job, full=True))


def source_json(source):
    return {'id': source.id, 'name': source.name, 'kind': source.kind, 'endpoint_url': source.endpoint_url,
            'title_keywords': json.loads(source.config_json).get('title_keywords', []),
            'enabled': source.enabled, 'priority': source.priority, 'last_success_at': iso(source.last_success_at), 'last_error': source.last_error}


@api.route('/generation/sources/', methods=['GET', 'POST'])
@api.route('/generation/sources/<int:source_id>/', methods=['PUT'])
@admin
def generation_sources(source_id=None):
    if request.method == 'GET':
        return jsonify({'list': [source_json(s) for s in AiNewsSource.query.order_by(AiNewsSource.priority.desc(), AiNewsSource.id).all()]})
    data = body()
    url = validate_url(data['endpoint_url'], resolve=False)
    name = data['name'].strip()
    if not name or len(name) > 128 or type(data.get('enabled', True)) is not bool:
        raise ValueError('来源名称或启用状态不正确')
    keywords = data.get('title_keywords')
    if keywords is not None and (not isinstance(keywords, list) or len(keywords) > 60
            or any(not isinstance(k, str) or not k.strip() or len(k) > 80 for k in keywords)):
        raise ValueError('标题关键词最多 60 个，每个 1–80 字')
    source = AiNewsSource.query.get(source_id) if source_id else None
    if source_id and (not source or source.kind != 'rss'):
        raise ValueError('历史手动来源不可改为自动采集来源，请新建 RSS 来源')
    if not source:
        source = AiNewsSource(kind='rss', config_json='{}', created_at=utcnow())
        db.session.add(source)
    source.name, source.endpoint_url = name, url
    if keywords is not None:
        options = json.loads(source.config_json)
        options['title_keywords'] = list(dict.fromkeys(k.strip() for k in keywords))
        source.config_json = encode(options)
    source.endpoint_hash = hashlib.sha256(url.encode()).digest()
    source.enabled, source.updated_at, source.priority = data.get('enabled', True), utcnow(), 100
    db.session.commit()
    return jsonify(source_json(source))


@api.route('/generation/sources/<int:source_id>/probe/', methods=['POST'])
@admin
def probe_generation_source(source_id):
    source = AiNewsSource.query.get(source_id)
    if not source or source.kind != 'rss':
        return not_found('RSS 来源不存在')
    rows = read_feed(source.endpoint_url)
    return jsonify({'count': len(rows), 'latest': [{'title': r['title'], 'published_at': iso(r['published_at'])} for r in rows[:3]]})


@api.route('/generated-contents/')
@admin
def generated_contents():
    page = max(1, int(request.args.get('page', 1)))
    query = Content.query
    if request.args.get('status'):
        query = query.filter_by(status=request.args['status'])
    rows = query.order_by(Content.edition.desc(), Content.id.desc()).paginate(page, per_page=20, error_out=False)
    return jsonify({'list': [content_json(c) for c in rows.items], 'total': rows.total, 'page': page})


@api.route('/generated-contents/<int:content_id>/', methods=['GET', 'PUT', 'POST'])
@admin
def generated_content(content_id):
    content = Content.query.filter_by(id=content_id).with_for_update().one_or_none() if request.method != 'GET' else Content.query.get(content_id)
    if not content:
        return not_found('内容不存在')
    if request.method == 'GET':
        return jsonify(content_json(content, True))
    data = body()
    if data.get('expected_revision') != content.current_revision:
        return jsonify({'message': '内容已更新，请刷新后重试'}), 409
    if request.method == 'PUT':
        previous = Revision.query.filter_by(content_id=content.id, revision=content.current_revision).one()
        doc = json.loads(previous.document_json)
        if doc['content'].get('schema_version') == 2 and data['content'].get('schema_version') != 2:
            raise ValueError('已分区的内容不能降级为旧格式')
        # Provenance and capture window are immutable; editors only change the article.
        doc.update(title=data['title'], summary=data['summary'], content=data['content'])
        ADAPTERS[content.content_type]['validate'](doc)
        content.current_revision += 1
        content.auto_publish = False
        if content.status != 'published':
            content.status = 'draft'
        db.session.add(Revision(content_id=content.id, revision=content.current_revision, origin='human',
            document_json=encode(doc), validated=True))
    elif data.get('action') == 'publish':
        engine.publish(content.id, content.current_revision)
    elif data.get('action') == 'withdraw':
        content.status, content.auto_publish = 'withdrawn', False
        if content.legacy_digest_id:
            AiDigest.query.get(content.legacy_digest_id).status = 'withdrawn'
    else:
        raise ValueError('操作不正确')
    content.updated_at = utcnow()
    db.session.commit()
    return jsonify(content_json(content, True))
