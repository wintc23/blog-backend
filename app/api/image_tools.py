"""Private jobs and assets; upload handoffs never grant account or generation access."""
import hashlib
import json
import os
import re
import secrets
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from uuid import uuid4
from sqlalchemy.exc import SQLAlchemyError
from flask import current_app, g, jsonify, request, redirect
from itsdangerous import URLSafeTimedSerializer, BadSignature
from . import api
from .decorators import login_required, permission_required
from .. import db
from ..models import Permission, User
from ..guest import consume_limits, client_address
from ..image_tool_models import ImageTool, ImageTask, ImageToolAsset, ImageToolItem, ImageToolSettings
from ..image_tools.config import encode, public_config, validate_config, validate_options
from ..image_tools import storage, provider
from ..image_tools.telemetry import record
from ..image_tools.policy import upload_policy


class ToolError(ValueError):
    def __init__(self, message, status=400, retry_after=None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def endpoint(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            response = current_app.make_response(fn(*args, **kwargs))
        except ValueError as error:
            db.session.rollback()
            response = current_app.make_response((jsonify(message=str(error), retry_after=getattr(error, 'retry_after', None)), getattr(error, 'status', 400)))
            if getattr(error, 'retry_after', None):
                response.headers['Retry-After'] = str(error.retry_after)
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.exception('Image tool database request failed')
            response = current_app.make_response((jsonify(message='图片服务暂不可用，请稍后重试。'), 503))
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response
    return wrapped


def body():
    if request.content_length is None or request.content_length > 32 * 1024:
        raise ToolError('请求内容过大', 413)
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise ToolError('请求格式不正确')
    return value


def signer():
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'], salt='image-tools-v1')


def task_for_owner(task_id, lock=False):
    query = ImageTask.query.filter_by(id=task_id, owner_id=g.current_user.id, deleted_at=None)
    task = (query.populate_existing().with_for_update() if lock else query).first()
    if not task:
        raise ToolError('任务不存在', 404)
    return task


def draft_only(task):
    if task.status != 'draft':
        raise ToolError('任务已开始，请复制设置创建新任务', 409)


def tool_json(tool, admin=False):
    config = json.loads(tool.config_json)
    return dict(slug=tool.slug, version=tool.version, enabled=tool.enabled, position=tool.position,
                config=config if admin else public_config(config, tool.slug))


def asset_json(asset, share_hash=None):
    ticket = signer().dumps(dict(asset=asset.id, share=share_hash))
    return dict(id=asset.id, name=asset.name, width=asset.width, height=asset.height, size=asset.byte_size,
                url='/image-assets/{}/?ticket={}'.format(asset.id, ticket))


def task_json(task):
    config = json.loads(task.snapshot_json)
    assets = ImageToolAsset.query.filter_by(task_id=task.id).order_by(ImageToolAsset.position, ImageToolAsset.created_at).all()
    items = ImageToolItem.query.filter_by(task_id=task.id).order_by(ImageToolItem.created_at, ImageToolItem.position, ImageToolItem.id).all()
    return dict(id=task.id, tool_slug=task.tool_slug, tool_version=task.tool_version, config=public_config(config, task.tool_slug),
        options=json.loads(task.options_json), status=task.status, created_at=task.created_at.isoformat() + 'Z',
        inputs=[asset_json(a) for a in assets if a.kind == 'input'], outputs=[asset_json(a) for a in assets if a.kind == 'output'],
        items=[dict(id=i.id, source_id=i.source_id, output_id=i.output_id, previous_id=i.previous_id, position=i.position, status=i.status, error=i.error) for i in items],
        sharing=bool(task.share_hash and task.share_until > datetime.utcnow()), quota=quota_for(g.current_user))


def settings_json():
    limits = ImageToolSettings.query.get(1)
    return dict(version=limits.version, global_per_minute=limits.global_per_minute, user_per_hour=limits.user_per_hour,
                upload_max_mb=limits.upload_max_mb, upload_max_megapixels=limits.upload_max_megapixels, processing_max_edge=limits.processing_max_edge)


def quota_for(user):
    limits = settings_json()
    if user.can(Permission.ADMIN):
        return dict(limits, exempt=True, remaining=None, retry_after=0)
    cutoff = datetime.utcnow() - timedelta(hours=1)
    rows = db.session.query(ImageToolItem).join(ImageTask, ImageToolItem.task_id == ImageTask.id).filter(
        ImageTask.owner_id == user.id, ImageToolItem.quota_exempt == False,
        db.or_(ImageToolItem.created_at > cutoff, ImageToolItem.started_at > cutoff)).all()
    expirations = sorted(max(row.created_at, row.started_at or row.created_at) + timedelta(hours=1) for row in rows)
    retry = max(1, int((expirations[0] - datetime.utcnow()).total_seconds()) + 1) if expirations else 0
    return dict(limits, exempt=False, remaining=max(0, limits['user_per_hour'] - len(rows)), retry_after=retry)


def reserve_quota(task, count):
    # Account locks make simultaneous submissions and retries share the same quota.
    User.query.filter_by(id=task.owner_id).update({User.auth_version: User.auth_version}, synchronize_session=False)
    user = User.query.filter_by(id=task.owner_id).populate_existing().with_for_update().one()
    quota = quota_for(user)
    if not quota['exempt'] and count > quota['user_per_hour']:
        raise ToolError('本次数量超过账号每小时 {} 张的上限，请减少图片数量'.format(quota['user_per_hour']))
    if not quota['exempt'] and count > quota['remaining']:
        raise ToolError('每小时最多生成 {} 张，当前还可提交 {} 张；约 {} 分钟后开始恢复额度。'.format(
            quota['user_per_hour'], quota['remaining'], max(1, (quota['retry_after'] + 59) // 60)), 429, quota['retry_after'] or 60)
    if user.is_guest and not quota['exempt']:
        # Shared across guest accounts; charged per image, including retries.
        # Uses the same transaction as queue creation, so failed submissions roll back.
        limited = consume_limits(client_address(), [('image-guest-generation-hour', quota['user_per_hour'], 3600)], amount=count)
        if limited is not None:
            raise ToolError('当前网络的游客生成额度已用完，请稍后再试或登录正式账号', 429,
                            int(limited.headers.get('Retry-After', 3600)))



@api.route('/image-tools/upload-policy/')
@endpoint
def image_upload_policy():
    return jsonify(upload_policy())


@api.route('/image-tools/')
@endpoint
def image_tools_list():
    tools = ImageTool.query.filter_by(enabled=True).order_by(ImageTool.position, ImageTool.slug).all()
    return jsonify(tools=[tool_json(t) for t in tools], ready=provider.ready())


@api.route('/image-tools/<slug>/')
@endpoint
def image_tool_detail(slug):
    tool = ImageTool.query.filter_by(slug=slug, enabled=True).first()
    if not tool:
        raise ToolError('工具不存在或暂未开放', 404)
    return jsonify(tool=tool_json(tool), ready=provider.ready())


@api.route('/image-tools/admin/templates/', methods=['GET', 'POST'])
@permission_required(Permission.ADMIN)
@endpoint
def image_tool_admin():
    if request.method == 'GET':
        return jsonify(tools=[tool_json(t, True) for t in ImageTool.query.order_by(ImageTool.position).all()], ready=provider.ready())
    data = body()
    slug = data.get('slug', '')
    if not isinstance(slug, str) or not re.fullmatch('[a-z][a-z0-9-]{0,63}', slug):
        raise ToolError('地址只支持小写字母、数字和连字符')
    if type(data.get('enabled')) is not bool or type(data.get('position', 0)) is not int:
        raise ToolError('发布状态或排序不正确')
    config = validate_config(data.get('config'))
    tool = ImageTool.query.filter_by(slug=slug).with_for_update().first()
    if tool:
        if data.get('version') != tool.version:
            raise ToolError('配置已被其他操作更新，请重新加载', 409)
        tool.version += 1
    else:
        tool = ImageTool(slug=slug, version=1)
        db.session.add(tool)
    tool.config_json, tool.enabled, tool.position = encode(config), data['enabled'], data.get('position', 0)
    tool.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify(tool=tool_json(tool, True))


@api.route('/image-tasks/', methods=['GET', 'POST'])
@login_required
@endpoint
def image_tasks():
    if request.method == 'GET':
        tasks = ImageTask.query.filter_by(owner_id=g.current_user.id, deleted_at=None).filter(ImageTask.status != 'draft').order_by(ImageTask.created_at.desc()).limit(100).all()
        return jsonify(tasks=[dict(id=t.id, name=json.loads(t.snapshot_json)['name'], status=t.status, tool_slug=t.tool_slug,
                                   created_at=t.created_at.isoformat() + 'Z') for t in tasks])
    if getattr(g.current_user, 'is_guest', False):
        limited = consume_limits(client_address(), [('image-guest-task-create', 30, 3600)])
        if limited is not None:
            return limited
    limited = consume_limits(str(g.current_user.id), [('image-task-create', 30, 3600)])
    if limited is not None:
        return limited
    slug = body().get('tool_slug')
    if not isinstance(slug, str) or not re.fullmatch('[a-z][a-z0-9-]{0,63}', slug):
        raise ToolError('工具地址不正确')
    tool = ImageTool.query.filter_by(slug=slug, enabled=True).first()
    if not tool:
        raise ToolError('工具不存在或暂未开放', 404)
    task = ImageTask(id=uuid4().hex, owner_id=g.current_user.id, tool_slug=tool.slug, tool_version=tool.version, snapshot_json=tool.config_json)
    db.session.add(task)
    db.session.commit()
    return jsonify(task=task_json(task)), 201


@api.route('/image-tasks/<task_id>/', methods=['GET', 'PATCH', 'DELETE'])
@login_required
@endpoint
def image_task(task_id):
    task = task_for_owner(task_id, request.method != 'GET')
    if request.method == 'PATCH':
        draft_only(task)
        data = body()
        config = json.loads(task.snapshot_json)
        # Drafts can save an empty prompt; the final submit validates required fields.
        task.options_json = encode(validate_options(dict(config, prompt_required=False), data.get('options', {})))
        if 'asset_order' in data:
            assets = ImageToolAsset.query.filter_by(task_id=task.id, kind='input').all()
            ids = data['asset_order']
            if not isinstance(ids, list) or any(not isinstance(i, str) for i in ids) or len(ids) != len(set(ids)) or set(ids) != {a.id for a in assets}:
                raise ToolError('图片列表已更新，请刷新后重试', 409)
            for asset in assets:
                asset.position = ids.index(asset.id)
        task.updated_at = datetime.utcnow()
        db.session.commit()
    elif request.method == 'DELETE':
        task.deleted_at = datetime.utcnow()
        task.upload_nonce = task.share_hash = None
        ImageToolItem.query.filter_by(task_id=task.id, status='queued').update(dict(status='cancelled'))
        db.session.commit()
        return jsonify(success=True)
    return jsonify(task=task_json(task))


@api.route('/image-tasks/<task_id>/assets/', methods=['POST'])
@login_required
@endpoint
def image_task_upload(task_id):
    return upload_into(task_for_owner(task_id, True))


def upload_into(task):
    draft_only(task)
    config = json.loads(task.snapshot_json)
    assets = ImageToolAsset.query.filter_by(task_id=task.id, kind='input').all()
    data = body()
    if data.get('action') == 'authorize':
        if len(assets) >= config['max_images']:
            raise ToolError('此任务最多上传 {} 张图片'.format(config['max_images']))
        owner = User.query.get(task.owner_id)
        if owner.is_guest:
            limited = consume_limits(client_address(), [('image-guest-upload', 100, 3600)])
            if limited is not None:
                return limited
        limited = consume_limits(str(task.owner_id), [('image-tool-upload', 100, 3600)])
        if limited is not None:
            return limited
        policy = upload_policy()
        size, mime, name = data.get('size'), data.get('mime'), data.get('name')
        if type(size) is not int or not 0 < size <= policy['max_bytes'] or mime not in ('image/jpeg', 'image/png', 'image/webp') or not isinstance(name, str):
            raise ToolError('上传限制已更新，请重新选择图片以自动优化后上传')
        asset_id = uuid4().hex
        key = storage.cloud.PREFIX + asset_id + '.original'
        token = storage.cloud.upload_token(key, size, mime)
        ticket = signer().dumps(dict(scope='cloud-upload', task=task.id, asset=asset_id, name=name[:180], policy=policy))
        db.session.commit()
        host = storage.cloud.setting('IMAGE_TOOLS_QINIU_UPLOAD_URL')
        if not host or not host.startswith('https://'):
            raise ToolError('图片上传服务尚未配置', 503)
        return jsonify(key=key, token=token, ticket=ticket, upload_url=host)
    if data.get('action') != 'complete':
        raise ToolError('请使用七牛云直传上传图片')
    try:
        value = signer().loads(data.get('ticket', ''), max_age=600)
    except BadSignature:
        raise ToolError('上传凭证已过期，请重新上传', 410)
    if value.get('scope') != 'cloud-upload' or value.get('task') != task.id:
        raise ToolError('上传凭证无效', 403)
    existing = ImageToolAsset.query.filter_by(id=value['asset'], task_id=task.id).first()
    if existing:
        return jsonify(id=existing.id, name=existing.name), 201
    if len(assets) >= config['max_images']:
        raise ToolError('此任务最多上传 {} 张图片'.format(config['max_images']))
    raw = storage.cloud.read(storage.cloud.PREFIX + value['asset'] + '.original', value.get('policy', upload_policy())['max_bytes'])
    asset = storage.store(task.id, raw, value['name'], position=len(assets), asset_id=value['asset'], original_uploaded=True, policy=value.get('policy'))
    task.updated_at = datetime.utcnow()
    record('upload_complete', task, count=1, source='phone' if request.headers.get('X-Image-Upload-Token') else 'local')
    db.session.commit()
    return jsonify(id=asset.id, name=asset.name), 201


@api.route('/image-tasks/<task_id>/assets/<asset_id>/', methods=['DELETE'])
@login_required
@endpoint
def image_task_remove_asset(task_id, asset_id):
    task = task_for_owner(task_id, True)
    draft_only(task)
    asset = ImageToolAsset.query.filter_by(task_id=task.id, id=asset_id, kind='input').first()
    if not asset:
        raise ToolError('图片不存在', 404)
    storage.discard(asset)
    db.session.delete(asset)
    db.session.commit()
    return jsonify(success=True)


@api.route('/image-tasks/<task_id>/submit/', methods=['POST'])
@login_required
@endpoint
def image_task_submit(task_id):
    task = task_for_owner(task_id, True)
    # Idempotent: a repeated submit can never create a second paid batch.
    if task.status != 'draft':
        return jsonify(task=task_json(task))
    tool = ImageTool.query.filter_by(slug=task.tool_slug, enabled=True).first()
    if not tool or not provider.ready():
        raise ToolError('当前工具暂不可用，请稍后再试', 503)
    config = json.loads(task.snapshot_json)
    options = validate_options(config, json.loads(task.options_json))
    assets = ImageToolAsset.query.filter_by(task_id=task.id, kind='input').order_by(ImageToolAsset.position).all()
    if not config['min_images'] <= len(assets) <= config['max_images']:
        raise ToolError('请上传 {} 至 {} 张图片'.format(config['min_images'], config['max_images']))
    sources = assets if config['mode'] == 'per_image' else [assets[0] if assets else None] * options['count']
    if ImageToolItem.query.filter_by(status='queued').count() + len(sources) > 1000:
        raise ToolError('当前排队任务较多，请稍后再试', 503)
    reserve_quota(task, len(sources))
    task.options_json, task.status, task.upload_nonce = encode(options), 'queued', None
    task.created_at = datetime.utcnow()
    record('submitted', task, count=len(sources))
    for position, source in enumerate(sources):
        db.session.add(ImageToolItem(id=uuid4().hex, task_id=task.id, source_id=source.id if source else None, position=position, quota_exempt=g.current_user.can(Permission.ADMIN)))
    db.session.commit()
    return jsonify(task=task_json(task))


@api.route('/image-tasks/<task_id>/items/<item_id>/retry/', methods=['POST'])
@login_required
@endpoint
def image_task_retry(task_id, item_id):
    task = task_for_owner(task_id, True)
    item = ImageToolItem.query.filter_by(task_id=task.id, id=item_id).first()
    if not item or item.status not in ('completed', 'failed', 'uncertain'):
        raise ToolError('这张图片暂不能重新生成', 409)
    existing = ImageToolItem.query.filter_by(previous_id=item.id).order_by(ImageToolItem.created_at.desc()).first()
    if existing and existing.status in ('queued', 'running'):
        return jsonify(task=task_json(task))
    if not ImageTool.query.filter_by(slug=task.tool_slug, enabled=True).first() or not provider.ready():
        raise ToolError('当前工具暂不可用', 503)
    reserve_quota(task, 1)
    db.session.add(ImageToolItem(id=uuid4().hex, task_id=task.id, source_id=item.source_id, previous_id=item.id, position=item.position, quota_exempt=g.current_user.can(Permission.ADMIN)))
    task.status = 'queued'
    record('retry', task, count=1)
    db.session.commit()
    return jsonify(task=task_json(task))


@api.route('/image-tasks/<task_id>/clone/', methods=['POST'])
@login_required
@endpoint
def image_task_clone(task_id):
    original = task_for_owner(task_id)
    if g.current_user.is_guest:
        limited = consume_limits(client_address(), [('image-guest-task-create', 30, 3600)])
        if limited is not None:
            return limited
    tool = ImageTool.query.filter_by(slug=original.tool_slug, enabled=True).first()
    if not tool:
        raise ToolError('工具暂未开放', 404)
    limited = consume_limits(str(g.current_user.id), [('image-task-create', 30, 3600)])
    if limited is not None:
        return limited
    # Keep the exact prior version, so copied controls match the user's settings.
    task = ImageTask(id=uuid4().hex, owner_id=g.current_user.id, tool_slug=original.tool_slug,
        tool_version=original.tool_version, snapshot_json=original.snapshot_json, options_json=original.options_json)
    db.session.add(task)
    db.session.flush()
    for source in ImageToolAsset.query.filter_by(task_id=original.id, kind='input').all():
        storage.store(task.id, storage.read(source, True), source.name, position=source.position)
    db.session.commit()
    return jsonify(task=task_json(task)), 201


@api.route('/image-tasks/<task_id>/handoff/', methods=['POST', 'DELETE'])
@login_required
@endpoint
def image_task_handoff(task_id):
    task = task_for_owner(task_id, True)
    draft_only(task)
    task.upload_nonce = uuid4().hex if request.method == 'POST' else None
    db.session.commit()
    token = signer().dumps(dict(task=task.id, nonce=task.upload_nonce, scope='upload')) if task.upload_nonce else None
    return jsonify(token=token, expires_in=900)


@api.route('/image-upload-session/', methods=['GET', 'POST'])
@endpoint
def image_upload_session():
    token = request.headers.get('X-Image-Upload-Token', '')
    try:
        value = signer().loads(token, max_age=900)
    except BadSignature:
        raise ToolError('上传入口已过期，请在原设备重新打开', 410)
    task = ImageTask.query.filter_by(id=value.get('task'), deleted_at=None).populate_existing().with_for_update().first()
    if value.get('scope') != 'upload' or not task or not task.upload_nonce or value.get('nonce') != task.upload_nonce:
        raise ToolError('上传入口已关闭', 410)
    draft_only(task)
    if request.method == 'POST':
        return upload_into(task)
    config = json.loads(task.snapshot_json)
    count = ImageToolAsset.query.filter_by(task_id=task.id, kind='input').count()
    return jsonify(name=config['name'], count=count, max_images=config['max_images'])


@api.route('/image-tasks/<task_id>/share/', methods=['POST', 'DELETE'])
@login_required
@endpoint
def image_task_share(task_id):
    task = task_for_owner(task_id, True)
    if request.method == 'DELETE':
        task.share_hash = None
        db.session.commit()
        return jsonify(success=True)
    ids = body().get('asset_ids')
    allowed = {a.id for a in ImageToolAsset.query.filter_by(task_id=task.id, kind='output').all()}
    if not isinstance(ids, list) or not ids or len(ids) > 40 or any(not isinstance(i, str) or i not in allowed for i in ids):
        raise ToolError('请选择已生成的图片')
    token = secrets.token_urlsafe(32)
    task.share_hash = hashlib.sha256(token.encode()).hexdigest()
    task.share_assets_json = encode(list(dict.fromkeys(ids)))
    task.share_until = datetime.utcnow() + timedelta(days=7)
    db.session.commit()
    return jsonify(token=token, expires_at=task.share_until.isoformat() + 'Z')


@api.route('/image-shares/<token>/')
@endpoint
def image_share(token):
    digest = hashlib.sha256(token.encode()).hexdigest()
    task = ImageTask.query.filter_by(share_hash=digest, deleted_at=None).first()
    if not task or task.share_until <= datetime.utcnow():
        raise ToolError('分享已过期或被取消', 404)
    ids = json.loads(task.share_assets_json)
    assets = {a.id: a for a in ImageToolAsset.query.filter(ImageToolAsset.id.in_(ids), ImageToolAsset.task_id == task.id).all()}
    return jsonify(name=json.loads(task.snapshot_json)['name'], tool_slug=task.tool_slug,
                   outputs=[asset_json(assets[i], digest) for i in ids if i in assets])


@api.route('/image-assets/<asset_id>/')
@endpoint
def image_asset(asset_id):
    try:
        ticket = signer().loads(request.args.get('ticket', ''), max_age=900)
    except BadSignature:
        raise ToolError('图片链接已过期，请刷新页面', 403)
    asset = ImageToolAsset.query.get(asset_id)
    task = ImageTask.query.get(asset.task_id) if asset else None
    if ticket.get('asset') != asset_id or not task or task.deleted_at:
        raise ToolError('图片不存在', 404)
    if ticket.get('share') and (ticket['share'] != task.share_hash or not task.share_until or task.share_until <= datetime.utcnow() or asset_id not in json.loads(task.share_assets_json or '[]')):
        raise ToolError('分享已失效', 403)
    if request.args.get('download') == '1':
        record('download', task, count=1)
        db.session.commit()
    url = storage.cloud.signed_url(storage.key(asset), filename=Path(asset.name).stem + '.png' if request.args.get('download') == '1' else None)
    # The browser follows this to Qiniu; image bytes never pass through the site.
    if request.args.get('resolve') == '1':
        return jsonify(url=url)
    return redirect(url, code=302)


@api.route('/image-tasks/<task_id>/download/')
@login_required
@endpoint
def image_task_download(task_id):
    task = task_for_owner(task_id)
    assets = ImageToolAsset.query.filter_by(task_id=task.id, kind='output').order_by(ImageToolAsset.position, ImageToolAsset.created_at).all()
    if not assets:
        raise ToolError('还没有可下载的结果')
    if sum(a.byte_size for a in assets) > 300 * 1024 * 1024:
        raise ToolError('结果文件较大，请逐张下载')
    record('download_all', task, count=len(assets))
    db.session.commit()
    return jsonify(files=[dict(url=storage.cloud.signed_url(storage.key(a), expires=600),
                   name='{:02d}_{}.png'.format(i + 1, Path(a.name).stem)) for i, a in enumerate(assets)])


@api.route('/image-tools/admin/jobs/')
@permission_required(Permission.ADMIN)
@endpoint
def image_tools_admin_jobs():
    tasks = ImageTask.query.filter_by(deleted_at=None).filter(ImageTask.status != 'draft').order_by(ImageTask.created_at.desc()).limit(100).all()
    return jsonify(model=provider.IMAGE_MODEL, limits=settings_json(),
                   jobs=[dict(id=t.id, owner_id=t.owner_id, name=json.loads(t.snapshot_json)['name'], status=t.status, created_at=t.created_at.isoformat() + 'Z') for t in tasks])


@api.route('/image-tools/admin/limits/', methods=['GET', 'PUT'])
@permission_required(Permission.ADMIN)
@endpoint
def image_tool_limits():
    if request.method == 'PUT':
        data = body()
        ImageToolSettings.query.filter_by(id=1).update({ImageToolSettings.id: ImageToolSettings.id}, synchronize_session=False)
        limits = ImageToolSettings.query.filter_by(id=1).populate_existing().with_for_update().one()
        if data.get('version') != limits.version:
            raise ToolError('限频配置已更新，请刷新后重试', 409)
        for name in ('global_per_minute', 'user_per_hour'):
            number = data.get(name)
            if type(number) is not int or not 1 <= number <= 10000:
                raise ToolError('限频数量必须为 1 至 10000 的整数')
            setattr(limits, name, number)
        for name, low, high in [('upload_max_mb', 1, 100), ('upload_max_megapixels', 1, 80), ('processing_max_edge', 512, 4096)]:
            number = data.get(name, getattr(limits, name))
            if type(number) is not int or not low <= number <= high:
                raise ToolError('图片配置超出允许范围')
            setattr(limits, name, number)
        limits.version += 1
        db.session.commit()
    return jsonify(limits=settings_json())
