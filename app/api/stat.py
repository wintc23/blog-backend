from flask import request, current_app, jsonify, g
from .. import db
from . import api
from ..models import Permission, StatEvent, Post, Product, Comment, Message, Like
from .decorators import *
from sqlalchemy import func
from datetime import datetime, timedelta
from ..socket import broadcast
from collections import defaultdict
from urllib.parse import urlparse
import json
import re

VISIT_EVENT_NAME = 'visitPage'


def record_business_event(name, params = None):
  """Record a trusted server-side action in the current request transaction."""
  ip = request.environ.get('HTTP_X_REAL_IP', request.remote_addr)
  event = StatEvent(
    name = name,
    params = json.dumps(params or {}, ensure_ascii = False),
    ip = ip,
  )
  if g.current_user:
    event.author = g.current_user
  db.session.add(event)
  return event


def get_visit_start_date():
  first_visit = get_visit_query().with_entities(StatEvent.timestamp).order_by(StatEvent.timestamp.asc()).first()
  if first_visit and first_visit[0]:
    value = first_visit[0]
    return datetime(value.year, value.month, value.day)
  return datetime.now()


def get_visit_query(start_time = None, end_time = None):
  query = StatEvent.query.filter_by(name = VISIT_EVENT_NAME)
  if start_time:
    query = query.filter(StatEvent.timestamp >= start_time)
  if end_time:
    query = query.filter(StatEvent.timestamp < end_time)
  return query


def parse_timestamp(value):
  if value in [None, '']:
    return None
  return datetime.fromtimestamp(float(value))


def get_week_start(value):
  week_start = value - timedelta(days = value.weekday())
  return datetime(week_start.year, week_start.month, week_start.day)


def build_site_stat_summary():
  start_date = get_visit_start_date()
  query = get_visit_query(start_time = start_date)
  visit_count = query.count()
  visitor_count = query.with_entities(StatEvent.visitor_id).filter(StatEvent.visitor_id.isnot(None)).distinct().count()
  return {
    'visit_count': visit_count,
    'visitor_count': visitor_count,
    'visit_start_date': start_date.strftime('%Y-%m-%d')
  }


@api.route('/save-stat-events/', methods=["POST"])
def save_stat_events():
  event_list = (request.get_json(silent = True) or {}).get('events', [])
  if not isinstance(event_list, list):
    return jsonify({'message': '事件格式错误', 'notify': False}), 400
  event_list = event_list[:50]
  ip = request.environ.get('HTTP_X_REAL_IP', request.remote_addr)
  has_visit_event = False
  for event_json in event_list:
    if not isinstance(event_json, dict):
      continue
    name = str(event_json.get('name') or '')[:32]
    if not name or not re.match(r'^[A-Za-z0-9_.-]+$', name):
      continue
    params = event_json.get('params') or '{}'
    if not isinstance(params, str):
      params = json.dumps(params, ensure_ascii = False)
    stat_event = StatEvent(
      name = name,
      params = params[:4096],
      visitor_id = str(event_json.get('visitor_id') or '')[:32] or None,
    )
    stat_event.ip = ip
    if stat_event.name == VISIT_EVENT_NAME:
      has_visit_event = True
    if g.current_user:
      stat_event.author = g.current_user
    db.session.add(stat_event)
  db.session.commit()
  if has_visit_event:
    broadcast('site-stat-summary', build_site_stat_summary())
  return jsonify({ "message": '打点事件收集成功', "notify": False })


@api.route('/get-site-stat-summary/')
def get_site_stat_summary():
  return jsonify(build_site_stat_summary())


@api.route('/get-site-stat-report/')
@permission_required(Permission.ADMIN)
def get_site_stat_report():
  start_time = parse_timestamp(request.args.get('start_time'))
  end_time = parse_timestamp(request.args.get('end_time'))
  granularity = request.args.get('granularity', 'day')
  if granularity not in ['day', 'week']:
    granularity = 'day'

  query = get_visit_query(start_time = start_time, end_time = end_time)
  event_list = query.with_entities(StatEvent.timestamp, StatEvent.visitor_id).order_by(StatEvent.timestamp.asc()).all()

  data_map = {}
  visitor_map = {}
  total_pv = 0
  total_uv_set = set()
  for timestamp, visitor_id in event_list:
    if granularity == 'week':
      bucket_date = get_week_start(timestamp)
    else:
      bucket_date = datetime(timestamp.year, timestamp.month, timestamp.day)
    bucket = bucket_date.strftime('%Y-%m-%d')
    if not bucket in data_map:
      data_map[bucket] = {
        'bucket': bucket,
        'pv': 0,
        'uv': 0,
      }
      visitor_map[bucket] = set()
    data_map[bucket]['pv'] += 1
    total_pv += 1
    if visitor_id:
      visitor_map[bucket].add(visitor_id)
      total_uv_set.add(visitor_id)

  result_list = []
  for bucket in sorted(data_map.keys(), reverse = True):
    data = data_map[bucket]
    data['uv'] = len(visitor_map[bucket])
    result_list.append(data)

  return jsonify({
    'summary': {
      'total_pv': total_pv,
      'total_uv': len(total_uv_set),
      'start_time': start_time.strftime('%Y-%m-%d %H:%M:%S') if start_time else '',
      'end_time': end_time.strftime('%Y-%m-%d %H:%M:%S') if end_time else '',
      'granularity': granularity,
    },
    'list': result_list,
  })


@api.route('/get-stat-events-info/')
@permission_required(Permission.ADMIN)
def get_stat_events_info():
  start_time = request.args.get('start_time', 0)
  end_time = request.args.get('end_time', 0)
  query = StatEvent.query
  if start_time and end_time:
    start_time = datetime.fromtimestamp(start_time)
    end_time = datetime.fromtimestamp(end_time)
    query = StatEvent.query.filter(StatEvent.timestamp > start_time, StatEvent.timestamp < end_time)
  stat_list = query.with_entities(
    StatEvent.name,
    func.count("*")
  ).group_by(StatEvent.name).all()
  return jsonify({ "stat_list": stat_list })


def _safe_params(value):
  try:
    data = json.loads(value or '{}')
    return data if isinstance(data, dict) else {}
  except (TypeError, ValueError):
    return {}


def _date_bucket(value):
  return value.strftime('%Y-%m-%d')


def _percent_change(current, previous):
  if not previous:
    return 100 if current else 0
  return round((current - previous) * 100.0 / previous, 1)


def _source_name(referrer, domain):
  if not referrer:
    return '直接访问'
  try:
    host = (urlparse(referrer).hostname or '').lower()
  except ValueError:
    return '其他来源'
  site_host = (urlparse(domain or '').hostname or '').lower()
  if not host:
    return '直接访问'
  if site_host and (host == site_host or host.endswith('.' + site_host)):
    return '站内跳转'
  if any(item in host for item in ['google.', 'bing.', 'baidu.', 'sogou.', 'so.com']):
    return '搜索引擎'
  if any(item in host for item in ['weixin.', 'wechat.', 'zhihu.', 'weibo.', 'x.com', 'twitter.', 'facebook.']):
    return '社交平台'
  return host


def _session_count(events):
  visitor_events = defaultdict(list)
  for event in events:
    key = event.visitor_id or 'anonymous:{0}'.format(event.id)
    visitor_events[key].append(event.timestamp)
  count = 0
  for timestamps in visitor_events.values():
    previous = None
    for timestamp in sorted(timestamps):
      if previous is None or timestamp - previous > timedelta(minutes = 30):
        count += 1
      previous = timestamp
  return count


def _analytics_range():
  end_time = parse_timestamp(request.args.get('end_time'))
  start_time = parse_timestamp(request.args.get('start_time'))
  if not end_time:
    now = datetime.now()
    end_time = datetime(now.year, now.month, now.day) + timedelta(days = 1)
  if not start_time:
    start_time = end_time - timedelta(days = 30)
  if start_time >= end_time:
    start_time = end_time - timedelta(days = 30)
  return start_time, end_time


@api.route('/analytics/dashboard/')
@permission_required(Permission.ADMIN)
def get_analytics_dashboard():
  start_time, end_time = _analytics_range()
  duration = end_time - start_time
  previous_start = start_time - duration

  visits = get_visit_query(start_time, end_time).order_by(StatEvent.timestamp.asc()).all()
  previous_visits = get_visit_query(previous_start, start_time).all()
  visits = [event for event in visits if not (_safe_params(event.params).get('fullPath') or '').startswith('/manage')]
  previous_visits = [event for event in previous_visits if not (_safe_params(event.params).get('fullPath') or '').startswith('/manage')]

  trend = {}
  cursor = datetime(start_time.year, start_time.month, start_time.day)
  while cursor < end_time:
    bucket = _date_bucket(cursor)
    trend[bucket] = {'bucket': bucket, 'pv': 0, 'uv': 0, 'comments': 0, 'messages': 0, 'likes': 0}
    cursor += timedelta(days = 1)

  visitor_buckets = defaultdict(set)
  page_map = defaultdict(lambda: {'pv': 0, 'visitors': set()})
  source_map = defaultdict(lambda: {'pv': 0, 'visitors': set()})
  all_visitors = set()
  for event in visits:
    params = _safe_params(event.params)
    path = (params.get('fullPath') or '/').split('?', 1)[0]
    bucket = _date_bucket(event.timestamp)
    if bucket not in trend:
      continue
    trend[bucket]['pv'] += 1
    if event.visitor_id:
      visitor_buckets[bucket].add(event.visitor_id)
      all_visitors.add(event.visitor_id)
      page_map[path]['visitors'].add(event.visitor_id)
    page_map[path]['pv'] += 1
    source = _source_name(params.get('from', ''), current_app.config.get('DOMAIN'))
    source_map[source]['pv'] += 1
    if event.visitor_id:
      source_map[source]['visitors'].add(event.visitor_id)

  comments = Comment.query.filter(Comment.timestamp >= start_time, Comment.timestamp < end_time).all()
  messages = Message.query.filter(Message.timestamp >= start_time, Message.timestamp < end_time).all()
  likes = Like.query.filter(Like.timestamp >= start_time, Like.timestamp < end_time).all()
  link_click_events = StatEvent.query.filter(
    StatEvent.name == 'product.link_click',
    StatEvent.timestamp >= start_time,
    StatEvent.timestamp < end_time,
  ).all()
  previous_comments = Comment.query.filter(Comment.timestamp >= previous_start, Comment.timestamp < start_time).count()
  previous_messages = Message.query.filter(Message.timestamp >= previous_start, Message.timestamp < start_time).count()
  previous_likes = Like.query.filter(Like.timestamp >= previous_start, Like.timestamp < start_time).count()
  for item, key in [(value, 'comments') for value in comments] + [(value, 'messages') for value in messages] + [(value, 'likes') for value in likes]:
    bucket = _date_bucket(item.timestamp)
    if bucket in trend:
      trend[bucket][key] += 1
  for bucket, visitors in visitor_buckets.items():
    trend[bucket]['uv'] = len(visitors)

  post_ids = set()
  for path in page_map:
    match = re.match(r'^/article/(\d+)$', path)
    if match:
      post_ids.add(int(match.group(1)))
  post_ids.update(item.post_id for item in comments if item.post_id)
  post_ids.update(item.post_id for item in likes if item.post_id)
  posts = {post.id: post for post in Post.query.filter(Post.id.in_(post_ids)).all()} if post_ids else {}
  comment_counts = defaultdict(int)
  like_counts = defaultdict(int)
  for item in comments:
    comment_counts[item.post_id] += 1
  for item in likes:
    like_counts[item.post_id] += 1
  content_rows = []
  for post_id in post_ids:
    post = posts.get(post_id)
    if not post:
      continue
    stats = page_map.get('/article/{0}'.format(post_id), {'pv': 0, 'visitors': set()})
    uv = len(stats['visitors'])
    interactions = comment_counts[post_id] + like_counts[post_id]
    content_rows.append({
      'id': post_id,
      'type': 'article',
      'title': post.title,
      'path': '/article/{0}'.format(post_id),
      'pv': stats['pv'],
      'uv': uv,
      'comments': comment_counts[post_id],
      'likes': like_counts[post_id],
      'interaction_rate': round(interactions * 100.0 / uv, 1) if uv else 0,
      'link_clicks': 0,
      'link_conversion_rate': 0,
    })
  product_click_counts = defaultdict(int)
  product_link_map = defaultdict(lambda: {'clicks': 0, 'visitors': set(), 'label': '', 'location': ''})
  click_product_ids = set()
  for event in link_click_events:
    params = _safe_params(event.params)
    try:
      product_id = int(params.get('productId') or params.get('product_id') or 0)
    except (TypeError, ValueError):
      product_id = 0
    if not product_id:
      continue
    link_key = str(params.get('linkKey') or params.get('link_key') or 'unknown')[:64]
    key = (product_id, link_key)
    product_link_map[key]['clicks'] += 1
    product_link_map[key]['label'] = str(params.get('linkLabel') or params.get('link_label') or link_key)[:128]
    product_link_map[key]['location'] = str(params.get('location') or '')[:64]
    if event.visitor_id:
      product_link_map[key]['visitors'].add(event.visitor_id)
    product_click_counts[product_id] += 1
    click_product_ids.add(product_id)
  products_by_id = {item.id: item for item in Product.query.filter(Product.id.in_(click_product_ids)).all()} if click_product_ids else {}
  for path, stats in page_map.items():
    match = re.match(r'^/products/([a-z0-9-]+)$', path)
    if not match:
      continue
    product = Product.query.filter_by(slug = match.group(1)).first()
    if product:
      uv = len(stats['visitors'])
      clicks = product_click_counts[product.id]
      content_rows.append({'id': product.id, 'type': 'product', 'title': product.name, 'path': path, 'pv': stats['pv'], 'uv': uv, 'comments': 0, 'likes': 0, 'interaction_rate': 0, 'link_clicks': clicks, 'link_conversion_rate': round(clicks * 100.0 / uv, 1) if uv else 0})

  total_pv = len(visits)
  total_uv = len(all_visitors)
  previous_uv = len(set(event.visitor_id for event in previous_visits if event.visitor_id))
  pending_comments = Comment.query.filter_by(hide = True).count()
  pending_messages = Message.query.filter_by(hide = True).count()
  metrics = {
    'pv': total_pv,
    'uv': total_uv,
    'sessions': _session_count(visits),
    'comments': len(comments),
    'messages': len(messages),
    'likes': len(likes),
    'pending_comments': pending_comments,
    'pending_messages': pending_messages,
  }
  previous_metrics = {
    'pv': len(previous_visits),
    'uv': previous_uv,
    'sessions': _session_count(previous_visits),
    'comments': previous_comments,
    'messages': previous_messages,
    'likes': previous_likes,
  }
  changes = {key: _percent_change(value, previous_metrics.get(key, 0)) for key, value in metrics.items() if key in previous_metrics}

  top_pages = sorted([
    {'path': path, 'pv': data['pv'], 'uv': len(data['visitors'])}
    for path, data in page_map.items()
  ], key = lambda item: (item['pv'], item['uv']), reverse = True)[:12]
  sources = sorted([
    {'name': name, 'pv': data['pv'], 'uv': len(data['visitors']), 'percentage': round(data['pv'] * 100.0 / total_pv, 1) if total_pv else 0}
    for name, data in source_map.items()
  ], key = lambda item: item['pv'], reverse = True)[:10]
  product_links = []
  for (product_id, link_key), stats in product_link_map.items():
    product = products_by_id.get(product_id)
    path = '/products/{0}'.format(product.slug) if product else ''
    product_uv = len(page_map[path]['visitors']) if path in page_map else 0
    product_links.append({
      'product_id': product_id,
      'product_name': product.name if product else '作品 #{0}'.format(product_id),
      'link_key': link_key,
      'link_label': stats['label'],
      'location': stats['location'],
      'clicks': stats['clicks'],
      'uv': len(stats['visitors']),
      'conversion_rate': round(stats['clicks'] * 100.0 / product_uv, 1) if product_uv else 0,
    })

  return jsonify({
    'range': {'start': start_time.strftime('%Y-%m-%d'), 'end': (end_time - timedelta(seconds = 1)).strftime('%Y-%m-%d')},
    'metrics': metrics,
    'changes': changes,
    'trend': [trend[key] for key in sorted(trend.keys())],
    'top_pages': top_pages,
    'top_content': sorted(content_rows, key = lambda item: (item['pv'], item['uv']), reverse = True)[:12],
    'sources': sources,
    'product_links': sorted(product_links, key = lambda item: item['clicks'], reverse = True),
    'interaction': {
      'comments': len(comments),
      'comment_replies': sum(1 for item in comments if item.response_id),
      'messages': len(messages),
      'message_replies': sum(1 for item in messages if item.response_id),
      'likes': len(likes),
      'pending_comments': metrics['pending_comments'],
      'pending_messages': metrics['pending_messages'],
    },
  })
