
from flask import request, current_app, jsonify, g
from .. import db
from . import api
from ..models import Topic, Permission
from .errors import *
from .decorators import *

@api.route('/get-topics/')
def get_topic_list():
  topics = Topic.query.all()
  topic_list = list(map(lambda topic: topic.to_json(), topics))
  return jsonify({ "list": topic_list })

@api.route('/add-topic/', methods = ['POST'])
@permission_required(Permission.WRITE)
def add_topic():
  title = request.json.get('title', '')
  topic = Topic(title = title)
  db.session.add(topic)
  return jsonify({
    "message": '添加成功',
    "notify": True
  })

@api.route('/delete-topic/<topic_id>')
@permission_required(Permission.ADMIN)
def delete_topic(topic_id):
  topic = Topic.query.get(topic_id)
  if not topic:
    return bad_request('专题不存在！')
  db.session.delete(topic)
  return jsonify({
    "message": "删除成功",
    "notify": True
  })

@api.route('/update-topic/', methods = ["POST"])
@permission_required(Permission.ADMIN)
def save_topic():
  topic_id = request.json.get('id')
  topic = Topic.query.get(topic_id)
  if not topic:
    return not_found('专题不存在！')
  for key in request.json:
    setattr(topic, key, request.json[key])
  db.session.add(topic)
  return jsonify({
    'message': '保存成功',
    'notify': True
  })
