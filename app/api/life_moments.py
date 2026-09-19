import json
import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from flask import g, jsonify, request
from . import api
from .decorators import permission_required
from .errors import bad_request, not_found
from .personal_profile import MOMENT_CATEGORIES, valid_url
from .. import db
from ..models import LifeMoment, PersonalProfile, Permission

LOCAL_TIMEZONE = timezone(timedelta(hours=8))


def moment_values(data, moment_id):
  if not isinstance(data, dict):
    raise ValueError('生活动态格式不正确')
  values = {'id': moment_id}
  for field, limit, default in (('text', 2000, ''), ('category', 16, 'daily'), ('location', 60, '')):
    value = data.get(field, default)
    if not isinstance(value, str) or len(value.strip()) > limit:
      raise ValueError('动态{}格式不正确或超过长度限制'.format(field))
    values[field] = value.strip()
  if not values['text']:
    raise ValueError('请填写动态内容')
  if values['category'] not in MOMENT_CATEGORIES:
    raise ValueError('动态分类不正确')
  occurred_at = data.get('occurred_at')
  if occurred_at is not None:
    try:
      if not isinstance(occurred_at, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})', occurred_at):
        raise ValueError()
      local = datetime.fromisoformat(occurred_at.replace('Z', '+00:00')).astimezone(LOCAL_TIMEZONE)
      values['date'] = local.date()
      values['occurred_at'] = local.astimezone(timezone.utc).replace(tzinfo=None)
    except (ValueError, OverflowError):
      raise ValueError('请填写有效的动态时间（包含时区）')
  else:
    # Historical records only have a date. Do not invent a clock time for them.
    date = data.get('date')
    if not isinstance(date, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date):
      raise ValueError('请填写有效的动态日期')
    values['date'] = datetime.strptime(date, '%Y-%m-%d').date()
    values['occurred_at'] = None
  images = data.get('images')
  if 'images' not in data:
    images = [{'url': data['image_url'], 'description': data.get('image_alt', '')}] if data.get('image_url') else []
  if not isinstance(images, list) or len(images) > 9:
    raise ValueError('每条动态最多添加 9 张图片')
  normalized = []
  for picture in images:
    if not isinstance(picture, dict):
      raise ValueError('图片格式不正确')
    url, description = picture.get('url'), picture.get('description', '')
    if not isinstance(url, str) or len(url) > 2048 or not valid_url(url) or re.search(r'[<>"\'\\\x00-\x20]', url):
      raise ValueError('请填写有效的 HTTP 或 HTTPS 图片地址')
    if not isinstance(description, str) or len(description.strip()) > 200:
      raise ValueError('每张图片的描述不能超过 200 字')
    is_public = picture.get('is_public', True)
    if type(is_public) is not bool:
      raise ValueError('图片可见性必须是公开或隐藏')
    normalized.append({'url': url, 'description': description.strip(), **({'is_public': is_public} if 'is_public' in picture else {})})
  values['images_json'] = json.dumps(normalized, ensure_ascii=False)
  if len(values['images_json'].encode('utf-8')) > 60000:
    raise ValueError('图片地址和描述内容过长')
  # Keep the first image in the legacy columns for compatibility with older clients.
  values['image_url'] = normalized[0]['url'] if normalized else ''
  values['image_alt'] = normalized[0]['description'][:120] if normalized else ''
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
  group_by = request.args.get('group_by')
  if group_by not in (None, 'date'):
    return bad_request('分组参数不正确', True)
  query = LifeMoment.query.order_by(LifeMoment.date.desc(), LifeMoment.occurred_at.desc(), LifeMoment.created_at.desc(), LifeMoment.id.desc())
  total = query.count()
  if group_by == 'date':
    dates = db.session.query(LifeMoment.date).distinct()
    total_dates = dates.count()
    page = min(page, max(1, (total_dates + per_page - 1) // per_page))
    selected = [row[0] for row in dates.order_by(LifeMoment.date.desc()).offset((page - 1) * per_page).limit(per_page).all()]
    moments = query.filter(LifeMoment.date.in_(selected)).all() if selected else []
    groups = {date.isoformat(): [] for date in selected}
    for moment in moments:
      groups[moment.date.isoformat()].append(moment.to_json(include_hidden=bool(g.current_user and g.current_user.can(Permission.ADMIN))))
    return jsonify({'groups': [{'date': date, 'moments': rows} for date, rows in groups.items()],
                    'total': total, 'total_dates': total_dates, 'page': page, 'per_page': per_page})
  page = min(page, max(1, (total + per_page - 1) // per_page))
  moments = query.offset((page - 1) * per_page).limit(per_page).all()
  return jsonify({'list': [moment.to_json(include_hidden=bool(g.current_user and g.current_user.can(Permission.ADMIN))) for moment in moments], 'total': total, 'page': page, 'per_page': per_page})


@api.route('/life-moments/<moment_id>/')
def get_life_moment(moment_id):
  moment = LifeMoment.query.get(moment_id)
  if moment is None:
    return not_found('找不到这条动态', True)
  return jsonify(moment.to_json(include_hidden=bool(g.current_user and g.current_user.can(Permission.ADMIN))))


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
  return jsonify(moment.to_json(include_hidden=bool(g.current_user and g.current_user.can(Permission.ADMIN)))), 201


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
  return jsonify(moment.to_json(include_hidden=bool(g.current_user and g.current_user.can(Permission.ADMIN))))


@api.route('/life-moments/<moment_id>/', methods=['DELETE'])
@permission_required(Permission.ADMIN)
def delete_life_moment(moment_id):
  moment = LifeMoment.query.get(moment_id)
  if moment is None:
    return not_found('找不到这条动态', True)
  profile = PersonalProfile.query.get(1)
  if profile and profile.moments_json:
    profile.moments_json = json.dumps([row for row in json.loads(profile.moments_json) if row.get('id') != moment_id], ensure_ascii=False)
  db.session.delete(moment)
  db.session.commit()
  return jsonify({'id': moment_id, 'message': '动态已删除'})


@api.after_request
def private_moment_response(response):
  if request.path.startswith(('/api/life-moments/', '/api/albums/', '/api/album-photos/')):
    response.headers['Cache-Control'] = 'private, no-store'
    response.vary.add('Authorization')
  return response


@api.route('/life-moments/<moment_id>/images/visibility/', methods=['PATCH'])
@permission_required(Permission.ADMIN)
def set_moment_image_visibility(moment_id):
  moment = LifeMoment.query.filter_by(id=moment_id).with_for_update().first()
  if moment is None:
    return not_found('找不到这条动态', True)
  data = request.get_json(silent=True)
  if not isinstance(data, dict) or type(data.get('is_public')) is not bool or type(data.get('index')) is not int:
    return bad_request('图片可见性参数不正确', True)
  pictures = moment.pictures()
  index = data['index']
  if index < 0 or index >= len(pictures) or pictures[index]['url'] != data.get('url'):
    return bad_request('图片顺序已改变，请刷新后重试', True)
  pictures[index]['is_public'] = data['is_public']
  moment.images_json = json.dumps(pictures, ensure_ascii=False)
  moment.updated_at = datetime.utcnow()
  db.session.commit()
  return jsonify(moment.to_json(include_hidden=True))
