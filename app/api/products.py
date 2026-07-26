import json
import re
from datetime import datetime

from flask import request, jsonify, g

from . import api
from .decorators import permission_required
from .errors import bad_request, not_found
from .. import db
from ..models import Permission, Product, ProductSection


JSON_LIST_FIELDS = {
  'highlights': 'highlights_json',
  'features': 'features_json',
  'steps': 'steps_json',
  'screenshots': 'screenshots_json',
  'links': 'links_json',
}

TEXT_FIELDS = [
  'name',
  'slug',
  'tagline',
  'summary',
  'platform',
  'version',
  'status',
  'status_label',
  'logo_url',
  'cover_url',
  'accent_color',
  'story_html',
]

SECTION_TYPES = {
  'feature_grid',
  'steps',
  'gallery',
  'rich_text',
  'image_text',
  'links',
  'callout',
}


def _is_admin():
  return g.current_user and g.current_user.can(Permission.ADMIN)


def _validate_product(data, product = None):
  name = (data.get('name') or (product.name if product else '') or '').strip()
  slug = (data.get('slug') or (product.slug if product else '') or '').strip().lower()
  if not name:
    return None, bad_request('产品名称不能为空', True)
  if not re.match(r'^[a-z0-9]+(?:-[a-z0-9]+)*$', slug):
    return None, bad_request('产品地址只能包含小写字母、数字和连字符', True)
  duplicate = Product.query.filter_by(slug = slug).first()
  if duplicate and (not product or duplicate.id != product.id):
    return None, bad_request('产品地址已存在', True)

  for key in JSON_LIST_FIELDS:
    value = data.get(key)
    if value is not None and not isinstance(value, list):
      return None, bad_request('{0} 必须是数组'.format(key), True)
  sections = data.get('sections')
  if sections is not None:
    if not isinstance(sections, list):
      return None, bad_request('sections 必须是数组', True)
    for index, section in enumerate(sections):
      if not isinstance(section, dict):
        return None, bad_request('第 {0} 个模块格式不正确'.format(index + 1), True)
      if section.get('type') not in SECTION_TYPES:
        return None, bad_request('不支持的产品模块类型', True)
      if section.get('content') is not None and not isinstance(section.get('content'), dict):
        return None, bad_request('模块内容格式不正确', True)
  return {'name': name, 'slug': slug}, None


def _apply_sections(product, sections):
  for section in product.sections.all():
    db.session.delete(section)
  for index, data in enumerate(sections or []):
    section = ProductSection(
      type = data.get('type'),
      title = (data.get('title') or '')[:128],
      subtitle = (data.get('subtitle') or '')[:128],
      layout = (data.get('layout') or 'default')[:32],
      content_json = json.dumps(data.get('content') or {}, ensure_ascii = False),
      visible = bool(data.get('visible', True)),
      sort = index,
      updated_at = datetime.utcnow()
    )
    product.sections.append(section)


def _apply_product(product, data, normalized):
  product.name = normalized['name']
  product.slug = normalized['slug']
  for key in TEXT_FIELDS:
    if key in ('name', 'slug'):
      continue
    if key in data:
      setattr(product, key, data.get(key) or '')
  for key, column in JSON_LIST_FIELDS.items():
    if key in data:
      setattr(product, column, json.dumps(data.get(key) or [], ensure_ascii = False))
  if 'published' in data:
    product.published = bool(data.get('published'))
  if 'featured' in data:
    product.featured = bool(data.get('featured'))
  if 'sort' in data:
    try:
      product.sort = int(data.get('sort') or 0)
    except (TypeError, ValueError):
      product.sort = 0
  if 'sections' in data:
    _apply_sections(product, data.get('sections'))
  product.updated_at = datetime.utcnow()


@api.route('/products/')
def get_products():
  query = Product.query
  if not _is_admin() or request.args.get('manage') != '1':
    query = query.filter_by(published = True)
  products = query.order_by(Product.featured.desc(), Product.sort.asc(), Product.created_at.desc()).all()
  return jsonify({'list': [product.to_json() for product in products]})


@api.route('/products/<slug>/')
def get_product(slug):
  product = Product.query.filter_by(slug = slug).first()
  if not product or (not product.published and not _is_admin()):
    return not_found('找不到该产品', True)
  return jsonify(product.to_json())


@api.route('/products/', methods = ['POST'])
@permission_required(Permission.ADMIN)
def create_product():
  data = request.get_json(silent = True) or {}
  normalized, error = _validate_product(data)
  if error:
    return error
  product = Product(created_at = datetime.utcnow())
  _apply_product(product, data, normalized)
  db.session.add(product)
  db.session.commit()
  return jsonify(product.to_json())


@api.route('/products/<int:product_id>/', methods = ['PUT'])
@permission_required(Permission.ADMIN)
def update_product(product_id):
  product = Product.query.get(product_id)
  if not product:
    return not_found('找不到该产品', True)
  data = request.get_json(silent = True) or {}
  normalized, error = _validate_product(data, product)
  if error:
    return error
  _apply_product(product, data, normalized)
  db.session.add(product)
  db.session.commit()
  return jsonify(product.to_json())


@api.route('/products/<int:product_id>/', methods = ['DELETE'])
@permission_required(Permission.ADMIN)
def delete_product(product_id):
  product = Product.query.get(product_id)
  if not product:
    return not_found('找不到该产品', True)
  db.session.delete(product)
  db.session.commit()
  return jsonify({'message': '删除成功', 'id': product_id})
