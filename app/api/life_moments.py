from datetime import datetime
from uuid import uuid4
from flask import jsonify, request
from . import api
from .decorators import permission_required
from .errors import bad_request, not_found
from .personal_profile import validate_moments
from .. import db
from ..models import LifeMoment, Permission


def moment_values(data, moment_id):
  if not isinstance(data, dict):
    raise ValueError('生活动态格式不正确')
  # Validate one independent record; there is no limit on the total number of posts.
  values = validate_moments([dict(data, id=moment_id)])[0]
  values['date'] = datetime.strptime(values['date'], '%Y-%m-%d').date()
  return values


@api.route('/life-moments/')
def get_life_moments():
  try:
    page = int(request.args.get('page', '1'))
    per_page = int(request.args.get('per_page', '12'))
    if page < 1 or per_page < 1 or per_page > 30:
      raise ValueError()
  except (ValueError, TypeError):
    return bad_request('分页参数不正确', True)
  query = LifeMoment.query.order_by(LifeMoment.date.desc(), LifeMoment.created_at.desc(), LifeMoment.id.desc())
  total = query.count()
  page = min(page, max(1, (total + per_page - 1) // per_page))
  moments = query.offset((page - 1) * per_page).limit(per_page).all()
  return jsonify({'list': [moment.to_json() for moment in moments], 'total': total, 'page': page, 'per_page': per_page})


@api.route('/life-moments/', methods=['POST'])
@permission_required(Permission.ADMIN)
def create_life_moment():
  try:
    values = moment_values(request.get_json(silent=True), str(uuid4()))
  except ValueError as error:
    return bad_request(str(error), True)
  moment = LifeMoment(**values)
  db.session.add(moment)
  db.session.commit()
  return jsonify(moment.to_json()), 201


@api.route('/life-moments/<moment_id>/', methods=['PUT'])
@permission_required(Permission.ADMIN)
def update_life_moment(moment_id):
  moment = LifeMoment.query.get(moment_id)
  if moment is None:
    return not_found('找不到这条动态', True)
  try:
    values = moment_values(request.get_json(silent=True), moment.id)
  except ValueError as error:
    return bad_request(str(error), True)
  for field, value in values.items():
    setattr(moment, field, value)
  moment.updated_at = datetime.utcnow()
  db.session.commit()
  return jsonify(moment.to_json())


@api.route('/life-moments/<moment_id>/', methods=['DELETE'])
@permission_required(Permission.ADMIN)
def delete_life_moment(moment_id):
  moment = LifeMoment.query.get(moment_id)
  if moment is None:
    return not_found('找不到这条动态', True)
  db.session.delete(moment)
  db.session.commit()
  return jsonify({'id': moment_id, 'message': '动态已删除'})
