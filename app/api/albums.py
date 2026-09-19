"""Albums reference durable cloud objects; neither copies nor signed URLs are persisted."""
import hashlib
import json
from datetime import datetime
from uuid import uuid4
from flask import g, jsonify, redirect, request, current_app
from itsdangerous import URLSafeTimedSerializer, BadSignature
from . import api
from .decorators import permission_required
from .image_tools import ToolError, endpoint, asset_json
from .. import db
from ..album_models import Album, AlbumPhoto, AlbumItem
from ..image_tool_models import ImageToolAsset, ImageTask
from ..image_tools import storage, cloud
from ..media_models import MediaAsset
from ..media import MAX_IMAGE_BYTES, prepare_image
from ..models import LifeMoment, User, StatEvent, Permission
from ..guest import consume_limits


def signer():
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'], salt='album-assets-v1')


def body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ToolError('请求格式不正确')
    return data


def paging():
    try:
        page = int(request.args.get('page', 1))
        if page < 1: raise ValueError()
        return page, 24
    except (TypeError, ValueError):
        raise ToolError('分页参数不正确')


def owned(album_id, lock=False):
    query = Album.query.filter_by(id=album_id, owner_id=g.current_user.id)
    album = (query.with_for_update() if lock else query).first()
    if not album: raise ToolError('画册不存在', 404)
    return album


def readable(album_id):
    album = Album.query.get(album_id)
    if not album or album.visibility != 'public' and (not g.current_user or not g.current_user.can(Permission.ADMIN) or album.owner_id != g.current_user.id):
        raise ToolError('画册不存在或未公开', 404)
    return album


def photo_json(photo, album=None, is_public=True):
    owner = bool(g.current_user and g.current_user.can(Permission.ADMIN) and g.current_user.id == photo.owner_id)
    ticket = signer().dumps({'photo': photo.id, 'album': album.id if album else None,
        'version': album.version if album else None, 'owner': owner})
    return dict(id=photo.id, name=photo.name, width=photo.width, height=photo.height, source_kind=photo.source_kind, is_public=is_public,
        url='/album-photos/{}/image/?ticket={}'.format(photo.id, ticket))


def album_json(album, detail=False):
    items = AlbumItem.query.filter_by(album_id=album.id).order_by(AlbumItem.position, AlbumItem.photo_id).all()
    can_view_hidden = bool(g.current_user and g.current_user.can(Permission.ADMIN) and g.current_user.id == album.owner_id)
    if not can_view_hidden:
        items = [item for item in items if item.is_public]
    visibility = {item.photo_id: item.is_public for item in items}
    cover_id = album.cover_id if any(i.photo_id == album.cover_id for i in items) else items[0].photo_id if items else None
    cover = AlbumPhoto.query.get(cover_id) if cover_id else None
    result = dict(id=album.id, title=album.title, description=album.description, visibility=album.visibility, version=album.version,
        count=len(items), editable=bool(g.current_user and g.current_user.can(Permission.ADMIN) and g.current_user.id == album.owner_id), cover=photo_json(cover, album, visibility[cover.id]) if cover else None,
        updated_at=album.updated_at.isoformat() + 'Z')
    if detail:
        result['photos'] = [photo_json(photo, album, item.is_public) for item in items for photo in [AlbumPhoto.query.get(item.photo_id)] if photo]
    return result


def record(event, **values):
    db.session.add(StatEvent(name='album.' + event, author_id=g.current_user.id,
        params=json.dumps(values, ensure_ascii=False)))


def touch(album):
    album.version += 1
    album.updated_at = datetime.utcnow()


def validate_values(data):
    title, description, visibility = data.get('title'), data.get('description', ''), data.get('visibility', 'private')
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 100 or not isinstance(description, str) or len(description) > 1000 or visibility not in ('private', 'public'):
        raise ToolError('请填写画册名称（最多 100 字）、简介和可见范围')
    return title.strip(), description.strip(), visibility


@api.route('/albums/', methods=['GET'])
@endpoint
def list_albums():
    page, size = paging()
    scope = request.args.get('scope', 'public')
    if scope not in ('mine', 'public'): raise ToolError('画册范围不正确')
    if scope == 'mine':
        if not g.current_user: raise ToolError('请先登录', 401)
        if not g.current_user.can(Permission.ADMIN): raise ToolError('仅管理员可管理画册', 403)
        query = Album.query.filter_by(owner_id=g.current_user.id)
    else: query = Album.query.filter_by(visibility='public')
    total = query.count()
    return jsonify(list=[album_json(a) for a in query.order_by(Album.updated_at.desc(), Album.id).offset((page - 1) * size).limit(size)], total=total, page=page, per_page=size)


@api.route('/albums/', methods=['POST'])
@permission_required(Permission.ADMIN)
@endpoint
def create_album():
    User.query.filter_by(id=g.current_user.id).with_for_update().first()
    if Album.query.filter_by(owner_id=g.current_user.id).count() >= 100: raise ToolError('每个账号最多创建 100 本画册')
    title, description, visibility = validate_values(body())
    album = Album(id=uuid4().hex, owner_id=g.current_user.id, title=title, description=description, visibility=visibility)
    db.session.add(album); record('create', visibility=visibility); db.session.commit()
    return jsonify(album_json(album, True)), 201


@api.route('/albums/<album_id>/', methods=['GET'])
@endpoint
def get_album(album_id):
    response = jsonify(album_json(readable(album_id), True))
    response.headers['Cache-Control'] = 'no-store'
    return response


@api.route('/albums/<album_id>/', methods=['PATCH', 'DELETE'])
@permission_required(Permission.ADMIN)
@endpoint
def edit_album(album_id):
    album = owned(album_id, True)
    if request.method == 'DELETE':
        AlbumItem.query.filter_by(album_id=album.id).delete()
        db.session.delete(album); record('delete'); db.session.commit()
        return jsonify(message='画册已删除，源图片与其他画册不受影响')
    data = body()
    if data.get('version') != album.version: raise ToolError('画册已更新，请刷新后重试', 409)
    if 'title' in data:
        previous_visibility = album.visibility
        album.title, album.description, album.visibility = validate_values(data)
        if previous_visibility != album.visibility: record('visibility', visibility=album.visibility)
    if 'cover_id' in data:
        if not AlbumItem.query.filter_by(album_id=album.id, photo_id=data['cover_id']).first(): raise ToolError('封面必须来自当前画册')
        album.cover_id = data['cover_id']
    if 'order' in data:
        order = data['order']
        items = AlbumItem.query.filter_by(album_id=album.id).all()
        if not isinstance(order, list) or not all(isinstance(i, str) for i in order) or len(order) != len(items) or set(order) != {i.photo_id for i in items}: raise ToolError('图片排序不正确')
        positions = {photo: index for index, photo in enumerate(order)}
        for item in items: item.position = positions[item.photo_id]
    touch(album); db.session.commit()
    return jsonify(album_json(album, True))


def import_photo(source):
    kind, source_id = source.get('type'), source.get('id')
    if not isinstance(source_id, str): raise ToolError('图片来源无效')
    if kind == 'photo':
        photo = AlbumPhoto.query.filter_by(id=source_id, owner_id=g.current_user.id).first()
        if not photo: raise ToolError('图片不存在', 404)
        return photo
    url, key, name, width, height = None, None, '图片', 0, 0
    if kind == 'generated':
        asset = ImageToolAsset.query.get(source_id)
        task = ImageTask.query.filter_by(id=asset.task_id, owner_id=g.current_user.id).with_for_update().first() if asset else None
        if not task or task.deleted_at: raise ToolError('只能添加自己的生图图片', 403)
        key, name, width, height = storage.key(asset), asset.name, asset.width, asset.height
    elif kind == 'moment':
        moment = LifeMoment.query.get(source_id)
        index = source.get('index')
        images = moment.pictures() if moment else []
        if type(index) is not int or index < 0 or index >= len(images): raise ToolError('动态图片不存在', 404)
        url, name = images[index]['url'], images[index].get('description') or '动态照片'
    elif kind == 'media':
        asset = MediaAsset.query.filter_by(id=source_id, owner_id=g.current_user.id, status='ready').with_for_update().first()
        if not asset: raise ToolError('只能添加自己的上传图片', 403)
        url, width, height = asset.url, asset.width, asset.height
    else: raise ToolError('不支持的图片来源')
    source_key = hashlib.sha256((key or url).encode()).hexdigest()
    photo = AlbumPhoto.query.filter_by(owner_id=g.current_user.id, source_key=source_key).first()
    if not photo:
        photo = AlbumPhoto(id=uuid4().hex, owner_id=g.current_user.id, source_key=source_key, source_kind=kind, url=url, cloud_key=key, name=name[:180], width=width, height=height)
        db.session.add(photo); db.session.flush()
    return photo


@api.route('/albums/<album_id>/photos/', methods=['POST'])
@permission_required(Permission.ADMIN)
@endpoint
def add_album_photos(album_id):
    User.query.filter_by(id=g.current_user.id).with_for_update().first()
    album = owned(album_id, True)
    sources = body().get('sources')
    if not isinstance(sources, list) or not 1 <= len(sources) <= 100 or not all(isinstance(s, dict) for s in sources): raise ToolError('请选择 1 至 100 张图片')
    existing = {i.photo_id for i in AlbumItem.query.filter_by(album_id=album.id)}
    if len(existing) + len(sources) > 500: raise ToolError('每本画册最多 500 张图片')
    added = 0
    position = db.session.query(db.func.max(AlbumItem.position)).filter_by(album_id=album.id).scalar() or 0
    for source in sources:
        photo = import_photo(source)
        if photo.id not in existing:
            position += 1; added += 1; existing.add(photo.id)
            is_public = True
            if source.get('type') == 'moment':
                is_public = LifeMoment.query.get(source['id']).pictures()[source['index']].get('is_public', True)
            db.session.add(AlbumItem(album_id=album.id, photo_id=photo.id, position=position, is_public=is_public))
    if added: record('add_photos', count=added)
    touch(album); db.session.commit()
    return jsonify(album_json(album, True))


@api.route('/albums/<album_id>/photos/<photo_id>/', methods=['PATCH', 'DELETE'])
@permission_required(Permission.ADMIN)
@endpoint
def remove_album_photo(album_id, photo_id):
    album = owned(album_id, True)
    if request.method == 'PATCH':
        data = body()
        if type(data.get('is_public')) is not bool: raise ToolError('图片可见性参数不正确')
        item = AlbumItem.query.filter_by(album_id=album.id, photo_id=photo_id).first()
        if not item: raise ToolError('图片不在当前画册', 404)
        item.is_public = data['is_public']
        touch(album); db.session.commit()
        return jsonify(album_json(album, True))
    AlbumItem.query.filter_by(album_id=album.id, photo_id=photo_id).delete()
    if album.cover_id == photo_id: album.cover_id = None
    touch(album); db.session.commit()
    return jsonify(album_json(album, True))


@api.route('/album-photos/<photo_id>/image/')
@endpoint
def album_photo_image(photo_id):
    try: ticket = signer().loads(request.args.get('ticket', ''), max_age=600)
    except BadSignature: raise ToolError('图片链接已过期，请刷新画册', 403)
    photo = AlbumPhoto.query.get(photo_id)
    if not photo or ticket.get('photo') != photo_id: raise ToolError('图片不存在', 404)
    album_id = ticket.get('album')
    if album_id:
        album = Album.query.get(album_id)
        if not album or not AlbumItem.query.filter_by(album_id=album_id, photo_id=photo_id).first(): raise ToolError('图片已从画册移除', 404)
        item = AlbumItem.query.filter_by(album_id=album_id, photo_id=photo_id).first()
        if not ticket.get('owner') and not item.is_public: raise ToolError('图片已隐藏', 403)
        if not ticket.get('owner') and (album.visibility != 'public' or album.version != ticket.get('version')): raise ToolError('画册已更新或不再公开', 403)
    elif not ticket.get('owner'): raise ToolError('没有图片访问权限', 403)
    url = cloud.signed_url(photo.cloud_key) if photo.cloud_key else photo.url
    response = jsonify(url=url) if request.args.get('resolve') == '1' else redirect(url, 302)
    response.headers['Cache-Control'] = 'no-store'
    return response


@api.route('/album-sources/')
@permission_required(Permission.ADMIN)
@endpoint
def album_sources():
    page, size = paging(); source = request.args.get('source', 'photo')
    if source == 'photo':
        query = AlbumPhoto.query.filter_by(owner_id=g.current_user.id).order_by(AlbumPhoto.created_at.desc(), AlbumPhoto.id)
        total = query.count(); rows = [dict(source={'type': 'photo', 'id': p.id}, **photo_json(p)) for p in query.offset((page - 1) * size).limit(size)]
    elif source == 'generated':
        query = ImageToolAsset.query.join(ImageTask, ImageTask.id == ImageToolAsset.task_id).filter(ImageTask.owner_id == g.current_user.id, ImageTask.deleted_at.is_(None), ImageToolAsset.kind == 'output').order_by(ImageToolAsset.created_at.desc(), ImageToolAsset.id)
        total = query.count(); rows = [dict(source={'type': 'generated', 'id': a.id}, **asset_json(a)) for a in query.offset((page - 1) * size).limit(size)]
    elif source == 'media':
        query = MediaAsset.query.filter_by(owner_id=g.current_user.id, status='ready').order_by(MediaAsset.created_at.desc(), MediaAsset.id)
        total = query.count(); rows = [dict(id=a.id, source={'type': 'media', 'id': a.id}, url=a.url, name='已上传图片', width=a.width, height=a.height) for a in query.offset((page - 1) * size).limit(size)]
    elif source == 'moment':
        # Date posts contain at most nine photos; bound reads before flattening.
        query = LifeMoment.query.filter(LifeMoment.image_url != '').order_by(LifeMoment.date.desc(), LifeMoment.created_at.desc(), LifeMoment.id)
        total = query.count(); rows = []
        for moment in query.offset((page - 1) * 6).limit(6):
            for index, image in enumerate(moment.pictures()):
                rows.append(dict(id='{}:{}'.format(moment.id, index), source={'type': 'moment', 'id': moment.id, 'index': index}, url=image['url'], name=image.get('description') or moment.date.isoformat(), width=0, height=0))
        size = 6
    else: raise ToolError('图片来源不正确')
    return jsonify(list=rows, total=total, page=page, per_page=size)


@api.route('/album-uploads/', methods=['POST'])
@permission_required(Permission.ADMIN)
@endpoint
def album_upload():
    data = body()
    if data.get('action') == 'authorize':
        limited = consume_limits(str(g.current_user.id), [('album-upload-hour', 30, 3600)])
        if limited is not None: return limited
        size, mime = data.get('size'), data.get('mime')
        if type(size) is not int or not 0 < size <= MAX_IMAGE_BYTES or mime not in ('image/jpeg', 'image/png', 'image/webp'): raise ToolError('图片需要先优化到 5 MB 内')
        photo_id = uuid4().hex; key = cloud.PREFIX + photo_id + '.original'
        ticket = signer().dumps(dict(upload=photo_id, owner=g.current_user.id, size=size))
        return jsonify(ticket=ticket, key=key, token=cloud.upload_token(key, size, mime), upload_url=cloud.setting('IMAGE_TOOLS_QINIU_UPLOAD_URL'))
    if data.get('action') != 'complete': raise ToolError('上传步骤不正确')
    try: ticket = signer().loads(data.get('ticket', ''), max_age=900)
    except BadSignature: raise ToolError('上传凭证已过期，请重试', 403)
    if ticket.get('owner') != g.current_user.id or not ticket.get('upload'): raise ToolError('上传凭证无效', 403)
    User.query.filter_by(id=g.current_user.id).with_for_update().first()
    photo_id = ticket['upload']; photo = AlbumPhoto.query.get(photo_id)
    if photo: return jsonify(photo_json(photo)), 200
    raw = cloud.read(cloud.PREFIX + photo_id + '.original', limit=ticket['size'])
    try: clean, extension, mime, width, height = prepare_image(raw)
    except ValueError as error: raise ToolError(str(error))
    key = cloud.PREFIX + photo_id + '.' + extension
    cloud.put(key, clean, mime)
    photo = AlbumPhoto(id=photo_id, owner_id=g.current_user.id, source_kind='upload', source_key=hashlib.sha256(key.encode()).hexdigest(), cloud_key=key, name='上传照片.' + extension, width=width, height=height)
    db.session.add(photo); record('upload', count=1); db.session.commit()
    return jsonify(photo_json(photo)), 201
