"""Idempotently adopt existing articles without changing public data."""
import json
from datetime import timedelta
from .. import db
from ..digest_models import AiDigest, AiDigestItem, AiDigestSettings
from ..generation_models import GenerationTask, GenerationTaskVersion, GeneratedContent, ContentRevision
from .configuration import default_config, encode, iso


def initialize():
    task = GenerationTask.query.filter_by(content_type='daily_digest', channel='ai-news', environment='production').first()
    if task is None:
        config = default_config()
        settings = AiDigestSettings.query.get(1)
        if settings:
            config.update(prompt=settings.prompt_template, min_chars=settings.target_min_chars,
                          max_chars=settings.target_max_chars, lookback_hours=settings.lookback_hours,
                          max_lookback_hours=settings.max_lookback_hours, publish_time=settings.publish_time.strftime('%H:%M'))
        task = GenerationTask(name='每日 AI 行业动态', content_type='daily_digest', channel='ai-news', environment='production', enabled=False)
        db.session.add(task)
        db.session.flush()
        db.session.add(GenerationTaskVersion(task_id=task.id, version=1, config_json=encode(config)))
    adopted = 0
    for digest in AiDigest.query.order_by(AiDigest.id).all():
        if GeneratedContent.query.filter_by(legacy_digest_id=digest.id).first():
            continue
        content = GeneratedContent(channel='ai-news', content_type='daily_digest', edition=digest.issue_date,
            task_id=task.id, environment='production', status=digest.status, current_revision=digest.content_version,
            published_revision=digest.content_version if digest.status == 'published' else None,
            legacy_digest_id=digest.id, scheduled_publish_at=digest.scheduled_publish_at,
            deadline_at=digest.scheduled_publish_at + timedelta(hours=3), auto_publish=False,
            created_at=digest.created_at, updated_at=digest.updated_at)
        db.session.add(content)
        db.session.flush()
        sources = {}
        for ref in AiDigestItem.query.filter_by(digest_id=digest.id, content_version=digest.content_version).all():
            sources[ref.item_id] = dict(json.loads(ref.evidence_snapshot_json), item_id=ref.item_id)
        doc = {'title': digest.title, 'summary': digest.summary, 'content': json.loads(digest.content_json),
               'sources': list(sources.values()), 'source_window_start': iso(digest.source_window_start),
               'source_window_end': iso(digest.source_window_end)}
        db.session.add(ContentRevision(content_id=content.id, revision=digest.content_version, origin='import',
            document_json=encode(doc), validated=True, created_at=digest.updated_at))
        adopted += 1
    db.session.commit()
    return {'task_id': task.id, 'adopted_contents': adopted}
