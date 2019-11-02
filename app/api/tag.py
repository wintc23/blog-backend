
from flask import request, current_app, jsonify, g
from .. import db
from . import api
from ..models import Tag, Permission
from .errors import *
from .decorators import *

@api.route('/get-tags/')
def get_tag_list():
  tags = Tag.query.all()
  tag_list = list(map(lambda tag: tag.to_json(), tags))
  return jsonify({ "list": tag_list })

@api.route('/add-tag/', methods = ['POST'])
@permission_required(Permission.WRITE)
def add_tag():
  title = request.json.get('title', '')
  tag = Tag(title = title)
  db.session.add(tag)
  return jsonify({
    "message": '添加成功',
    "notify": True
  })

@api.route('/delete-tag/<tag_id>')
@permission_required(Permission.ADMIN)
def delete_tag(tag_id):
  tag = Tag.query.get(tag_id)
  if not tag:
    return bad_request('标签不存在！')
  db.session.delete(tag)
  return jsonify({
    "message": "删除成功",
    "notify": True
  })

@api.route('/update-tag/', methods = ["POST"])
@permission_required(Permission.ADMIN)
def save_tag():
  tag_id = request.json.get('id')
  tag = Tag.query.get(tag_id)
  if not tag:
    return not_found('标签不存在！')
  for key in request.json:
    setattr(tag, key, request.json[key])
  db.session.add(tag)
  return jsonify({
    'message': '保存成功',
    'notify': True
  })
