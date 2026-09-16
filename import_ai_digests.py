"""Import reviewed, sourced backfill articles; dry run unless --apply is supplied.

Use the configured database/storage. No publishing or email side effects.
"""
import argparse
import copy
import hashlib
import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from app import create_app, db
from app.digest_models import AiDigest, AiDigestItem, AiDigestRun, AiDigestSettings, AiNewsItem, AiNewsSource


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def sha(value):
    return hashlib.sha256(value.encode()).digest()


def utc(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Timestamp must include timezone')
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def validate(rows, root):
    dates = set()
    for row in rows:
        day = date.fromisoformat(row['issue_date'])
        assert day not in dates, 'Duplicate issue date'
        dates.add(day)
        assert row['timezone'] == 'Asia/Shanghai'
        cutoff = utc(row['scheduled_publish_at'])
        assert cutoff == datetime.combine(day, time(1)), 'Expected Beijing 09:00'
        assert utc(row['source_window_end']) == cutoff
        assert utc(row['source_window_start']) == cutoff - timedelta(hours=72)
        content = row['content']
        assert content['schema_version'] == 1 and content['sections'] and content['takeaways']
        block_ids = set()
        for block in content['sections']:
            assert block['id'] not in block_ids
            block_ids.add(block['id'])
            assert block['paragraphs'] and block['sources']
            for source in block['sources']:
                source_day = date.fromisoformat(source['published_date'])
                assert day - timedelta(days=3) <= source_day < day, 'Source outside date window'
                assert urlsplit(source['url']).scheme == 'https'
                if source.get('published_at'):
                    assert utc(source['published_at']) < cutoff, 'Source published after edition cutoff'
        images = [content['cover']] + [s['image'] for s in content['sections'] if s.get('image')]
        for image in images:
            filename = (root / image['local_path']).with_suffix('.png').resolve()
            assert root in filename.parents and filename.is_file(), 'Image missing or outside artifact directory'
            assert image['rights'] == 'original' and image['alt']


def import_rows(rows, root):
    from qiniu import put_file
    from app.qiniu import get_token
    from flask import current_app
    now = datetime.utcnow()
    for row in rows:
        if AiDigest.query.filter_by(issue_date=date.fromisoformat(row['issue_date'])).first():
            raise ValueError('An edition already exists; refusing to overwrite: ' + row['issue_date'])
    # Upload immutable, content-addressed files before opening the write transaction.
    # Retry reuses the same keys; old pictures are never deleted.
    for row in rows:
        content = row['content']
        for image in [content['cover']] + [s['image'] for s in content['sections'] if s.get('image')]:
            filename = (root / image.pop('local_path')).with_suffix('.png')
            digest = hashlib.sha256(filename.read_bytes()).hexdigest()[:24]
            key = 'ai-digest/{}/{}.png'.format(row['issue_date'], digest)
            result, info = put_file(get_token(key), key, str(filename), mime_type='image/png')
            if not result or info.status_code != 200:
                raise RuntimeError('Image upload failed: ' + filename.name)
            image['url'] = current_app.config['QI_NIU_LINK_URL'].rstrip('/') + '/' + key
    settings = AiDigestSettings.query.get(1)
    if settings is None:
        settings = AiDigestSettings(id=1, title='AI 行业动态', timezone='Asia/Shanghai', publish_time=time(9),
            auto_publish=False, email_enabled=False, lookback_hours=24, max_lookback_hours=72,
            target_min_chars=700, target_max_chars=1500,
            preferences_json=encode({'language': 'zh-CN', 'primary_sources_first': True, 'facts_and_analysis_separate': True}),
            generator_json=encode({'provider': None, 'model': None, 'credential_ref': None}),
            prompt_template='每天整理一篇简洁的图文 AI 行业动态。只采用可核查的一手资料，保留原文日期与链接；不捏造事实，不把旧消息当新消息，推断标为 AI 简评。', updated_at=now)
        db.session.add(settings)
    elif settings.timezone != 'Asia/Shanghai' or settings.publish_time != time(9):
        raise ValueError('Existing schedule differs; refusing to change it during import')
    manifest = []
    for row in rows:
        content = row['content']
        issue = AiDigest(issue_date=date.fromisoformat(row['issue_date']), timezone=row['timezone'],
            slug=row['slug'], title=row['title'], summary=row['summary'], content_json='{}',
            content_version=1, status='draft', scheduled_publish_at=utc(row['scheduled_publish_at']),
            source_window_start=utc(row['source_window_start']), source_window_end=utc(row['source_window_end']),
            created_at=now, updated_at=now)
        db.session.add(issue)
        db.session.flush()
        for position, block in enumerate(content['sections']):
            for source in block['sources']:
                url = source['url']
                parsed = urlsplit(url)
                endpoint = '{}://{}'.format(parsed.scheme, parsed.netloc)
                feed = AiNewsSource.query.filter_by(endpoint_hash=sha(endpoint)).first()
                if not feed:
                    feed = AiNewsSource(name=parsed.netloc, kind='webpage', endpoint_url=endpoint,
                        endpoint_hash=sha(endpoint), homepage_url=endpoint, enabled=True, priority=100,
                        config_json=encode({'collection': 'manual_verified_backfill'}), created_at=now, updated_at=now)
                    db.session.add(feed)
                    db.session.flush()
                if feed.endpoint_url != endpoint:
                    raise ValueError('Source hash collision')
                evidence = {'publisher': source['publisher'], 'title': source['title'], 'url': url,
                    'published_date': source['published_date'], 'published_at': source.get('published_at'),
                    'date_precision': 'datetime' if source.get('published_at') else 'date',
                    'facts': block['paragraphs'], 'checked_at': now.isoformat()+'Z',
                    'verification': 'Primary source checked during backfill. Explicit timestamps preserved when available; not a historical page snapshot.'}
                item = AiNewsItem.query.filter_by(url_hash=sha(url)).first()
                if not item:
                    item = AiNewsItem(source_id=feed.id, canonical_url=url, url_hash=sha(url), title=source['title'],
                        published_date=date.fromisoformat(source['published_date']),
                        published_at=utc(source['published_at']) if source.get('published_at') else None,
                        date_precision=evidence['date_precision'],
                        first_seen_at=now, last_seen_at=now, language='en', event_key=block['id'],
                        evidence_json=encode(evidence), content_hash=sha(encode(evidence)), selection_status='selected')
                    db.session.add(item)
                    db.session.flush()
                if item.canonical_url != url:
                    raise ValueError('Item hash collision')
                source['item_id'] = item.id
                db.session.add(AiDigestItem(digest_id=issue.id, content_version=1, block_id=block['id'],
                    item_id=item.id, position=position, evidence_snapshot_json=encode(evidence)))
        issue.content_json = encode(content)
        db.session.add(AiDigestRun(run_key='backfill-20260916-'+row['issue_date'], digest_id=issue.id,
            issue_date=issue.issue_date, attempt=1, trigger_kind='backfill', stage='persist', status='succeeded',
            settings_snapshot_json=encode({'timezone': settings.timezone, 'publish_time': '09:00', 'auto_publish': False, 'email_enabled': False}),
            result_json=encode({'section_count': len(content['sections']), 'generation': 'AI-assisted sourced backfill', 'scheduled_job': False}),
            started_at=now, finished_at=datetime.utcnow()))
        manifest.append({'id':issue.id,'issue_date':row['issue_date'],'status':'draft','scheduled_publish_at':row['scheduled_publish_at'],'title':row['title']})
    db.session.commit()
    for row, record in zip(rows, manifest):
        row.update(record)
        row['created_at'] = now.isoformat()+'Z'
        (root/row['issue_date']/'digest.json').write_text(json.dumps(row, ensure_ascii=False, indent=2)+'\n')
    (root/'import-result.json').write_text(json.dumps({'created_at':now.isoformat()+'Z','issues':manifest}, ensure_ascii=False, indent=2)+'\n')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    path = args.input.resolve()
    rows = json.loads(path.read_text())
    validate(rows, path.parent)
    if not args.apply:
        print(encode({'validated_editions':len(rows),'write':False}))
    else:
        app = create_app('development')
        with app.app_context():
            print(encode(import_rows(copy.deepcopy(rows), path.parent)))
