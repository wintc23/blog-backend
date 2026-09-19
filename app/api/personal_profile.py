import json
import re
from datetime import datetime
from urllib.parse import urlsplit
from uuid import UUID

from flask import jsonify, request

from . import api
from .decorators import permission_required
from .errors import bad_request
from .. import db
from ..models import Permission, PersonalProfile, Role, LifeMoment


FIELDS = {
  'display_name': ('显示名称', 64),
  'avatar_url': ('头像地址', 2048),
  'tagline': ('一句话简介', 255),
  'introduction': ('介绍开头', 500),
  'bio': ('个人介绍', 10000),
}
CONTACT_FIELDS = {
  'contact_email': ('联系邮箱', 254),
  'wechat_id': ('微信号', 128),
  'wechat_qr_url': ('微信二维码地址', 2048),
  'contact_note': ('联系说明', 500),
}
SECTION_FIELDS = {'portfolio_introduction': ('作品栏目简介', 500)}
LINK_GROUPS = ('navigation', 'community', 'education', 'work')
LINK_ICONS = ('link', 'github', 'zhihu', 'weibo', 'youtube', 'linkedin', 'sysu', 'bytedance')
MOMENT_CATEGORIES = ('mountain', 'hiking', 'travel', 'daily')


def validate_moments(moments):
  if not isinstance(moments, list) or len(moments) > 12:
    raise ValueError('最多可以添加 12 条生活动态')
  normalized = []
  ids = set()
  for moment in moments:
    if not isinstance(moment, dict):
      raise ValueError('生活动态格式不正确')
    item = {}
    for field, label, limit in (
      ('id', '动态标识', 36), ('date', '动态日期', 10),
      ('category', '动态分类', 16), ('text', '动态短文', 280),
      ('image_url', '动态照片地址', 2048), ('image_alt', '照片描述', 120),
      ('location', '动态地点', 60),
    ):
      value = moment.get(field, '')
      if not isinstance(value, str) or len(value) > limit:
        raise ValueError('{}必须是 {} 字以内的文本'.format(label, limit))
      item[field] = value.strip()
    try:
      item['id'] = str(UUID(item['id']))
    except ValueError:
      raise ValueError('动态标识不正确')
    if item['id'] in ids:
      raise ValueError('动态标识不能重复')
    ids.add(item['id'])
    try:
      if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', item['date']):
        raise ValueError()
      datetime.strptime(item['date'], '%Y-%m-%d')
    except ValueError:
      raise ValueError('请填写有效的动态日期')
    if item['category'] not in MOMENT_CATEGORIES:
      raise ValueError('动态分类不正确')
    if not item['text']:
      raise ValueError('请填写动态短文')
    if not valid_url(item['image_url']):
      raise ValueError('动态照片须使用 http 或 https 地址')
    normalized.append(item)
  return sorted(normalized, key=lambda item: item['date'], reverse=True)


def valid_url(value, contact=False):
  if re.search(r'[\s\\\x00-\x1f\x7f]', value):
    return False
  if contact and value.startswith('/') and not value.startswith('//'):
    return True
  try:
    parsed = urlsplit(value)
    if parsed.scheme in ('http', 'https'):
      parsed.port  # Validate a supplied port as well as the hostname.
      return bool(parsed.hostname) and not parsed.username and not parsed.password
    if contact and parsed.scheme == 'mailto':
      return bool(re.fullmatch(r'[^@/?#]+@[^@/?#]+\.[^@/?#]+', parsed.path))
  except ValueError:
    pass
  return False


def validate_profile(data):
  if not isinstance(data, dict):
    raise ValueError('个人信息格式不正确')
  values = {}
  for field, (label, limit) in {**FIELDS, **CONTACT_FIELDS, **SECTION_FIELDS}.items():
    # Older editors must preserve optional fields they do not know about.
    if field not in FIELDS and field not in data:
      continue
    value = data.get(field, '')
    if not isinstance(value, str) or len(value) > limit:
      raise ValueError('{}必须是 {} 字以内的文本'.format(label, limit))
    values[field] = value.strip()
  if not values['display_name']:
    raise ValueError('请填写显示名称')
  # Older editors must preserve the saved site name when omitting this field.
  if 'site_name' in data:
    site_name = data['site_name']
    if not isinstance(site_name, str) or not site_name.strip() or len(site_name) > 128:
      raise ValueError('站点名称须为 1 到 128 字')
    values['site_name'] = site_name.strip()
  if values['avatar_url'] and not valid_url(values['avatar_url']):
    raise ValueError('头像地址须使用 http 或 https')
  if values.get('wechat_qr_url') and not valid_url(values['wechat_qr_url']):
    raise ValueError('微信二维码地址须使用 http 或 https')
  if values.get('contact_email') and not re.fullmatch(r'[^@\s<>?&#]+@[^@\s<>?&#]+\.[^@\s<>?&#]+', values['contact_email']):
    raise ValueError('请填写有效的联系邮箱')

  links = data.get('links', [])
  if not isinstance(links, list) or len(links) > 8:
    raise ValueError('最多可以添加 8 个联系链接')
  normalized_links = []
  for link in links:
    if not isinstance(link, dict):
      raise ValueError('联系链接格式不正确')
    label, url = link.get('label'), link.get('url')
    if not isinstance(label, str) or not label.strip() or len(label) > 60:
      raise ValueError('链接名称须为 1 到 60 字')
    if not isinstance(url, str) or len(url) > 2048 or not valid_url(url.strip(), contact=True):
      raise ValueError('链接须为 http、https、mailto 地址或站内路径')
    normalized = {'label': label.strip(), 'url': url.strip()}
    for field, allowed, name in (
      ('group', LINK_GROUPS, '链接分组'), ('icon', LINK_ICONS, '链接图标'),
    ):
      # Legacy links without presentation metadata remain valid.
      if field in link:
        if not isinstance(link[field], str) or link[field] not in allowed:
          raise ValueError('{}不正确'.format(name))
        normalized[field] = link[field]
    normalized_links.append(normalized)
  values['links_json'] = json.dumps(normalized_links, ensure_ascii=False)
  # Older profile editors must not erase life moments they do not know about.
  if 'moments' in data:
    moments_json = json.dumps(validate_moments(data['moments']), ensure_ascii=False)
    if len(moments_json.encode('utf-8')) > 60000:
      raise ValueError('动态内容过长，请缩短照片地址或减少动态数量')
    values['moments_json'] = moments_json
  return values


@api.route('/personal-profile/')
def get_personal_profile():
  profile = PersonalProfile.query.get(1)
  # A fresh installation is editable without creating data during a GET.
  result = (profile or PersonalProfile(id=1)).to_json()
  # Migration backups must not expose an image hidden in the current record.
  if result['moments']:
    current = {moment.id: moment for moment in LifeMoment.query.filter(LifeMoment.id.in_([row['id'] for row in result['moments']])).all()}
    result['moments'] = [current[row['id']].to_json() if row['id'] in current else row for row in result['moments']]
  response = jsonify(result)
  response.headers['Cache-Control'] = 'no-store'
  return response


@api.route('/personal-profile/', methods=['PUT'])
@permission_required(Permission.ADMIN)
def save_personal_profile():
  try:
    values = validate_profile(request.get_json(silent=True))
  except ValueError as error:
    return bad_request(str(error), True)
  profile = PersonalProfile.query.get(1)
  if profile is None:
    profile = PersonalProfile(id=1)
  for field, value in values.items():
    setattr(profile, field, value)
  profile.updated_at = datetime.utcnow()
  db.session.add(profile)
  # The same administrator is exposed by /get-user-info/.
  role = Role.query.filter_by(name='Administrator').first()
  owner = role.users.order_by('id').first() if role else None
  if owner:
    owner.username = values['display_name']
  db.session.commit()
  return jsonify(profile.to_json())
