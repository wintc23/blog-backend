from datetime import datetime, timedelta
from uuid import uuid4
from flask import g, jsonify, request
from . import api
from .decorators import login_required
from .. import db
from ..guest import client_address, consume_limits
from ..media import MAX_IMAGE_BYTES, cleanup_images, image_url, prepare_image, put_image
from ..media_models import MediaAsset
from ..models import Permission


@api.route('/media/images/', methods=['POST'])
@login_required
def upload_image():
    if not g.current_user.can(Permission.COMMENT):
        return jsonify({'message': '没有上传权限'}), 403
    # Bound multipart parsing too, not just the extracted file contents.
    if request.content_length is None or request.content_length > MAX_IMAGE_BYTES + 64 * 1024:
        return jsonify({'message': '每张图片最大 5 MB'}), 413
    limited = consume_limits(str(g.current_user.id), [('image-upload-user-hour', 30, 3600), ('image-upload-user-day', 100, 86400)])
    if limited is not None:
        return limited
    limited = consume_limits(client_address(), [('image-upload-ip-hour', 60, 3600)])
    if limited is not None:
        return limited
    db.session.commit()
    upload = request.files.get('image')
    if upload is None:
        return jsonify({'message': '请选择图片'}), 400
    try:
        data, extension, mime, width, height = prepare_image(upload.stream.read(MAX_IMAGE_BYTES + 1))
    except ValueError as error:
        return jsonify({'message': str(error)}), 400
    asset_id = uuid4().hex
    key = 'managed-images/{}.{}'.format(asset_id, extension)
    asset = MediaAsset(id=asset_id, storage_key=key, url=image_url(key), owner_id=g.current_user.id,
        mime_type=mime, byte_size=len(data), width=width, height=height,
        delete_after=datetime.utcnow() + timedelta(hours=24))
    db.session.add(asset)
    db.session.commit()
    try:
        put_image(key, data, mime)
    except Exception:
        # A timeout may still have uploaded the object. Keep it in the cleanup queue.
        asset.status, asset.delete_after = 'orphan', datetime.utcnow()
        db.session.commit()
        return jsonify({'message': '图片上传失败，请稍后重试'}), 503
    asset.status = 'ready'
    db.session.commit()
    response = jsonify({'id': asset.id, 'url': asset.url, 'width': width, 'height': height, 'size': len(data), 'max_size': MAX_IMAGE_BYTES})
    response.headers['Cache-Control'] = 'no-store'
    # Also collects older abandoned uploads, without touching this fresh asset.
    g.media_cleanup_needed = True
    return response, 201
