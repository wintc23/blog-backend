from flask import request, current_app, jsonify, g
from .. import db
from . import api
from ..models import Permission, StatEvent
from .decorators import *
from sqlalchemy import func
from datetime import datetime

@api.route('/save-stat-events/', methods=["POST"])
def save_stat_events():
  event_list = request.json.get('events', [])
  ip = request.environ.get('HTTP_X_REAL_IP', request.remote_addr)
  for event_json in event_list:
    stat_event = StatEvent(**event_json)
    stat_event.ip = ip
    if g.current_user:
      stat_event.author = g.current_user
    db.session.add(stat_event)
  return jsonify({ "message": '打点事件收集成功', "notify": False })


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
