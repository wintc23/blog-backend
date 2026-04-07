from flask import request, current_app, jsonify, g
from .. import db
from . import api
from ..models import Permission, StatEvent
from .decorators import *
from sqlalchemy import func
from datetime import datetime, timedelta
from ..socket import broadcast

VISIT_EVENT_NAME = 'visitPage'


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
  visit_count = get_visit_query(start_time = start_date).count()
  return {
    'visit_count': visit_count,
    'visit_start_date': start_date.strftime('%Y-%m-%d')
  }


@api.route('/save-stat-events/', methods=["POST"])
def save_stat_events():
  event_list = request.json.get('events', [])
  ip = request.environ.get('HTTP_X_REAL_IP', request.remote_addr)
  has_visit_event = False
  for event_json in event_list:
    stat_event = StatEvent(**event_json)
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
