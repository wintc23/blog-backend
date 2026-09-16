import hashlib
import html
import json
import re
from datetime import date, timedelta
from xml.etree import ElementTree as ET
from urllib.parse import urljoin, urlsplit, urlunsplit
from dateutil import parser, tz
from .. import db
from ..digest_models import AiNewsSource, AiNewsItem
from .configuration import GenerationError, encode, iso, utcnow
from .network import fetch, validate_url


def archived_edition(task, edition, include_item_ids=None):
    from ..generation_models import GeneratedContent, ContentRevision
    content = GeneratedContent.query.filter_by(channel=task.channel, content_type=task.content_type,
        environment=task.environment, edition=edition).first()
    if not content:
        raise GenerationError('edition_missing', '该期没有可用于重生成的历史内容')
    revision = ContentRevision.query.filter_by(content_id=content.id,
        revision=content.published_revision or content.current_revision).one()
    document = json.loads(revision.document_json)
    rows = []
    for source in document.get('sources', []):
        excerpt = source.get('excerpt') or '\n'.join(source.get('facts', []))
        if excerpt:
            rows.append(dict(source, excerpt=excerpt))
    if not rows:
        raise GenerationError('insufficient_sources', '该期未保存可用的原始资料或已核实事实')
    start = parser.isoparse(document['source_window_start']).astimezone(tz.UTC).replace(tzinfo=None)
    end = parser.isoparse(document['source_window_end']).astimezone(tz.UTC).replace(tzinfo=None)
    known = {row['item_id'] for row in rows}
    for item_id in include_item_ids or []:
        item = AiNewsItem.query.get(item_id)
        if not item or not item.published_at or not start <= item.published_at <= end:
            raise ValueError('补充来源必须存在，且发布时间在本期原采集窗口内')
        evidence = json.loads(item.evidence_json)
        if not evidence.get('excerpt') or evidence.get('url') != item.canonical_url or evidence.get('published_at') != iso(item.published_at):
            raise ValueError('补充来源须保存正文摘要、准确发布时间与一致的原始链接')
        if item_id not in known:
            rows.append(dict(evidence, item_id=item.id, source_id=item.source_id))
            known.add(item_id)
    return {'sources': rows, 'source_window_start': document['source_window_start'],
            'source_window_end': document['source_window_end'], 'collection_errors': [],
            'collection_mode': 'supplemented_evidence' if include_item_ids else 'archived_evidence',
            'included_item_ids': include_item_ids or [], 'source_revision_id': revision.id}, document['content'].get('cover')


def plain(value):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', value or ''))).strip()


def canonical(url):
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or '/', parsed.query, ''))


def parse_feed(data, endpoint):
    # HTML declarations inside CDATA are article text, not XML declarations.
    xml_markup = re.sub(br'<!\[CDATA\[.*?\]\]>', b'', data, flags=re.S)
    if b'<!DOCTYPE' in xml_markup.upper() or b'<!ENTITY' in xml_markup.upper():
        raise ValueError('不支持包含实体声明的 Feed')
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        raise ValueError('来源不是有效的 RSS / Atom')
    tag = lambda node: node.tag.rsplit('}', 1)[-1]
    if tag(root) not in ('rss', 'feed', 'RDF'):
        raise ValueError('来源不是 RSS / Atom')
    rows = []
    for entry in (el for el in root.iter() if tag(el) in ('item', 'entry')):
        values = {}
        link = ''
        for child in entry:
            key = tag(child)
            value = ' '.join(child.itertext())
            values.setdefault(key, value)
            if key == 'link' and child.attrib.get('rel', 'alternate') == 'alternate':
                link = child.attrib.get('href') or value
        raw_date = values.get('pubDate') or values.get('published') or values.get('date')
        # An Atom updated timestamp is not necessarily its publication time.
        if not raw_date or not link or not values.get('title'):
            continue
        try:
            published = parser.parse(raw_date)
            if published.tzinfo is None:
                continue
            published_date = published.date()
            published = published.astimezone(tz.UTC).replace(tzinfo=None)
            url = canonical(urljoin(endpoint, link.strip()))
            validate_url(url, resolve=False)
        except (ValueError, OverflowError):
            continue
        excerpt = plain(values.get('encoded') or values.get('content') or values.get('description') or values.get('summary'))[:10000]
        if len(excerpt) < 40:
            continue
        rows.append({'url': url, 'title': plain(values['title'])[:512], 'published_at': published, 'published_date': published_date.isoformat(),
                     'excerpt': excerpt, 'external_id': (values.get('id') or values.get('guid') or url)[:512]})
        if len(rows) >= 200:
            break
    return rows


def title_matches(title, keywords):
    # Latin words use boundaries: "AI" must not match "chair" or "paid".
    return not keywords or any(re.search(r'(?<![a-z0-9])' + re.escape(k) + r'(?![a-z0-9])', title, re.I)
                              if k.isascii() else k.casefold() in title.casefold() for k in keywords)


def collect(config, cutoff, capture_only=False):
    captured_at = utcnow()
    feeds = AiNewsSource.query.filter(AiNewsSource.id.in_(config['source_ids']), AiNewsSource.enabled.is_(True),
                                     AiNewsSource.kind == 'rss').order_by(AiNewsSource.priority.desc(), AiNewsSource.id).all()
    if not feeds:
        raise GenerationError('no_sources', '请先配置并选择已启用的 RSS / Atom 来源')
    earliest = cutoff - timedelta(hours=config['max_lookback_hours'])
    errors = []
    for feed in feeds:
        try:
            data, _ = fetch(feed.endpoint_url, headers={
                'User-Agent': 'Mozilla/5.0 (compatible; WintcNews/1.0; +https://wintc.top/ai-news)',
                'Accept': 'application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9',
            })
            rows = parse_feed(data, feed.endpoint_url)
            keywords = json.loads(feed.config_json).get('title_keywords', [])
            for row in rows:
                if not earliest <= row['published_at'] <= cutoff or not title_matches(row['title'], keywords):
                    continue
                digest = hashlib.sha256(row['url'].encode()).digest()
                item = AiNewsItem.query.filter_by(url_hash=digest).first()
                evidence = {'publisher': feed.name, 'title': row['title'], 'url': row['url'],
                            'published_at': iso(row['published_at']), 'published_date': row['published_date'],
                            'excerpt': row['excerpt'], 'checked_at': iso(captured_at), 'verification': 'RSS/Atom 原始正文或摘要快照；回溯采集不代表历史网页状态'}
                if not item:
                    item = AiNewsItem(source_id=feed.id, canonical_url=row['url'], url_hash=digest,
                                     first_seen_at=captured_at, selection_status='pending')
                    db.session.add(item)
                elif item.canonical_url != row['url']:
                    raise ValueError('来源 URL 哈希冲突')
                item.external_id, item.title = row['external_id'], row['title']
                item.published_at, item.published_date = row['published_at'], date.fromisoformat(row['published_date'])
                item.date_precision, item.last_seen_at = 'datetime', captured_at
                item.evidence_json, item.content_hash = encode(evidence), hashlib.sha256(encode(evidence).encode()).digest()
            feed.last_success_at, feed.last_error = captured_at, None
            db.session.commit()
        except (GenerationError, ValueError) as exc:
            db.session.rollback()
            feed.last_error = str(exc)[:1000]
            db.session.commit()
            errors.append({'source_id': feed.id, 'message': str(exc)})
    if capture_only:
        return {'collection_errors': errors}
    # Round-robin candidates keep prolific general feeds from hiding smaller sources.
    # This is an input diversity policy, never a publication quota.
    pools = []
    for feed in feeds:
        pool = (AiNewsItem.query.filter(AiNewsItem.source_id == feed.id,
                AiNewsItem.published_at >= earliest, AiNewsItem.published_at <= cutoff)
                .order_by(AiNewsItem.published_at.desc(), AiNewsItem.id.desc()).all())
        keywords = json.loads(feed.config_json).get('title_keywords', [])
        pools.append([item for item in pool if title_matches(item.title, keywords)
                      and json.loads(item.evidence_json).get('excerpt')][:config['max_items']])
    items = []
    for offset in range(config['max_items']):
        for pool in pools:
            if offset < len(pool) and len(items) < config['max_items']:
                items.append(pool[offset])
    if not items:
        raise GenerationError('insufficient_sources', '采集窗口内没有足够的可核查资料', True)
    sources = []
    for item in items:
        evidence = json.loads(item.evidence_json)
        if not evidence.get('excerpt'):
            continue
        sources.append(dict(evidence, item_id=item.id, source_id=item.source_id))
    if not sources:
        raise GenerationError('insufficient_sources', '来源中缺少可供生成使用的正文或摘要', True)
    return {'source_window_start': iso(earliest), 'source_window_end': iso(cutoff), 'sources': sources, 'collection_errors': errors}
