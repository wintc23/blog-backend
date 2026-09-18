"""One provider request per claimed item; ambiguous requests are never auto-replayed."""
import json
import os
import time
from datetime import datetime, timedelta
from uuid import uuid4
import requests
from .. import db
from ..image_tool_models import ImageTool, ImageTask, ImageToolItem, ImageToolAsset, ImageToolSettings
from . import provider, storage
from .telemetry import record
from .config import DEFAULTS, encode


def seed():
    if not ImageToolSettings.query.get(1):
        db.session.add(ImageToolSettings(id=1))
    for position, (slug, config) in enumerate(DEFAULTS.items()):
        if not ImageTool.query.get(slug):
            db.session.add(ImageTool(slug=slug, version=1, enabled=True, position=position, config_json=encode(config)))
    db.session.commit()


def refresh_status(task_id):
    task = ImageTask.query.get(task_id)
    if not task or task.deleted_at:
        return
    states = [row.status for row in ImageToolItem.query.filter_by(task_id=task_id).all()]
    task.status = ('running' if 'running' in states else 'queued' if 'queued' in states else
                   'completed' if states and all(s == 'completed' for s in states) else
                   'partial' if 'completed' in states else 'failed')
    task.updated_at = datetime.utcnow()


def recover():
    now = datetime.utcnow()
    expired = ImageToolItem.query.filter(ImageToolItem.status == 'running', ImageToolItem.lease_until < now).all()
    for item in expired:
        task_id = item.task_id
        changed = ImageToolItem.query.filter(ImageToolItem.id == item.id, ImageToolItem.status == 'running', ImageToolItem.lease_until < now).update(
            dict(status='uncertain', error='执行中断，生成结果待确认；重新生成会再次占用额度', lease_token=None), synchronize_session=False)
        if changed:
            db.session.expire_all()
            refresh_status(task_id)
            record('result', ImageTask.query.get(task_id), status='uncertain', count=1)
    db.session.commit()


def run_one():
    recover()
    # A shared settings-row lock serializes reservations across all worker processes.
    ImageToolSettings.query.filter_by(id=1).update({ImageToolSettings.id: ImageToolSettings.id}, synchronize_session=False)
    limits = ImageToolSettings.query.filter_by(id=1).populate_existing().with_for_update().one()
    now = datetime.utcnow()
    global_used = ImageToolItem.query.filter(ImageToolItem.quota_exempt == False, ImageToolItem.started_at > now - timedelta(minutes=1)).count()
    candidates = ImageToolItem.query.filter_by(status='queued').order_by(ImageToolItem.created_at, ImageToolItem.position).limit(200).all()
    admin = ImageToolItem.query.filter_by(status='queued', quota_exempt=True).order_by(ImageToolItem.created_at).first()
    if admin and admin not in candidates:
        candidates.append(admin)
    row = None
    for candidate in candidates:
        if candidate.quota_exempt:
            row = candidate
            break
        if global_used >= limits.global_per_minute:
            continue
        owner_id = ImageTask.query.get(candidate.task_id).owner_id
        user_used = db.session.query(ImageToolItem).join(ImageTask, ImageToolItem.task_id == ImageTask.id).filter(
            ImageTask.owner_id == owner_id, ImageToolItem.quota_exempt == False, ImageToolItem.started_at > now - timedelta(hours=1)).count()
        if user_used < limits.user_per_hour:
            row = candidate
            break
    if not row:
        db.session.rollback()
        return False
    item_id, token = row.id, uuid4().hex
    claimed = ImageToolItem.query.filter_by(id=item_id, status='queued').update(dict(
        status='running', started_at=now, lease_token=token, lease_until=now + timedelta(minutes=15)), synchronize_session=False)
    db.session.commit()
    if not claimed:
        return True
    item = ImageToolItem.query.get(item_id)
    task = ImageTask.query.get(item.task_id)
    task_id = item.task_id
    if not task or task.deleted_at or not ImageTool.query.filter_by(slug=task.tool_slug, enabled=True).first():
        item.status, item.error = 'cancelled', '任务已删除或工具已关闭'
        refresh_status(task_id)
        db.session.commit()
        return True
    config, options = json.loads(task.snapshot_json), json.loads(task.options_json)
    source = ImageToolAsset.query.get(item.source_id) if item.source_id else None
    choices = '\n'.join('{}：{}'.format(field['label'], options['fields'].get(field['key'], field['default'])) for field in config['fields'])
    prompt = '{}\n{}\n用户补充要求：{}\n画幅要求：{}，保留主体完整，不拉伸人物；需要时扩展背景。'.format(
        config['instruction'], choices, options['prompt'], options['ratio'])
    source_name = source.name if source else config['name']
    position = item.position
    refresh_status(task_id)
    db.session.commit()
    if source:
        # Load all attributes used by the adapter before releasing DB resources.
        source.id, source.width, source.height
        db.session.expunge(source)
    db.session.remove()
    try:
        data = provider.generate(prompt, options['ratio'], source, item_id)
        outcome, error = 'completed', None
    except (requests.RequestException, TimeoutError):
        data, outcome, error = None, 'uncertain', '连接中断或超时，结果待确认；重新生成会再次占用额度'
    except Exception:
        data, outcome, error = None, 'failed', '生成失败，请稍后重试；原图已保留'
    task = ImageTask.query.filter_by(id=task_id).populate_existing().with_for_update().first()
    item = ImageToolItem.query.filter_by(id=item_id, status='running', lease_token=token).first()
    if not item:
        db.session.rollback()
        return True
    if not task or task.deleted_at:
        item.status = 'cancelled'
    else:
        if data:
            try:
                asset = storage.store(task_id, data, '{}_{}.png'.format(os.path.splitext(source_name)[0], config['name']), 'output', position)
                item.output_id = asset.id
            except ValueError:
                outcome, error = 'failed', '返回图片无效，请稍后重试'
        item.status, item.error = outcome, error
        record('result', task, count=1, status=outcome, duration_ms=max(0, int((datetime.utcnow() - item.started_at).total_seconds() * 1000)))
        refresh_status(task_id)
    item.finished_at, item.lease_token, item.lease_until = datetime.utcnow(), None, None
    db.session.commit()
    return True


def cleanup():
    """Delete tombstoned jobs and abandoned drafts; completed jobs require explicit deletion."""
    cutoff = datetime.utcnow() - timedelta(days=7)
    tasks = ImageTask.query.filter(db.or_(ImageTask.deleted_at.isnot(None), db.and_(ImageTask.status == 'draft', ImageTask.updated_at < cutoff))).limit(100).all()
    for candidate in tasks:
        task = ImageTask.query.filter_by(id=candidate.id).populate_existing().with_for_update().first()
        if not task or not (task.deleted_at or task.status == 'draft' and task.updated_at < cutoff):
            continue
        if ImageToolItem.query.filter_by(task_id=task.id, status='running').count():
            continue
        # Keep accounting rows until both submission and execution windows expire.
        quota_cutoff = datetime.utcnow() - timedelta(hours=1)
        if ImageToolItem.query.filter(ImageToolItem.task_id == task.id, db.or_(
                ImageToolItem.created_at > quota_cutoff,
                ImageToolItem.started_at > quota_cutoff)).count():
            continue
        for asset in ImageToolAsset.query.filter_by(task_id=task.id).all():
            storage.discard(asset)
            db.session.delete(asset)
        ImageToolItem.query.filter_by(task_id=task.id).delete()
        db.session.delete(task)
    db.session.commit()
    # Orphaned uploads (including unused direct-upload tickets) expire after a day.
    known = {a.id for a in ImageToolAsset.query.all()}
    cutoff = (time.time() - 86400) * 10000000
    for obj in storage.cloud.objects():
        name = obj['key'][len(storage.cloud.PREFIX):]
        if name.split('.')[0] not in known and obj['putTime'] < cutoff:
            storage.cloud.delete(obj['key'])
