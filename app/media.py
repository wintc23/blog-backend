"""Image validation, transactional references, and retryable object cleanup."""
import io
import re
import warnings
from datetime import datetime, timedelta
from urllib.parse import urlsplit
from uuid import uuid4

from flask import current_app, g, has_app_context, has_request_context
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import event, or_
from sqlalchemy.orm import Session

from . import db
from .media_models import MediaAsset, MediaReference

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 24000000
IMAGE_PREFIX = 'managed-images/'
KEY_PATTERN = re.compile(r'^(?:managed-images/[a-f0-9]{32}\.(?:jpg|png|webp)|[a-f0-9]{32}(?:\.(?:jpg|jpeg|png|webp))?|generated-content/[a-f0-9]{64}\.png)$')
URL_PATTERN = re.compile(r'https?://[^\s<>"\'\[\]()]+')
TRACKED_FIELDS = {
    'users': ('avatar',),
    'album_photos': ('url',),
    'posts': ('body_html', 'abstract_image', 'abstract'),
    'comments': ('body',), 'messages': ('body',),
    'life_moments': ('image_url', 'images_json'),
    'personal_profiles': ('avatar_url', 'bio', 'moments_json', 'wechat_qr_url'),
    'products': ('logo_url', 'cover_url', 'story_html', 'screenshots_json', 'features_json', 'highlights_json', 'steps_json'),
    'product_sections': ('content_json',),
    'ai_digests': ('content_json', 'mail_snapshot_json'),
    # Saved revisions and sent newsletters are still real references.
    'content_revisions': ('document_json',),
}


class MediaReferenceError(ValueError):
    pass


def image_url(key):
    return current_app.config['QI_NIU_LINK_URL'].rstrip('/') + '/' + key


def managed_key(url):
    if not isinstance(url, str):
        return None
    try:
        parsed, base = urlsplit(url), urlsplit(current_app.config.get('QI_NIU_LINK_URL') or '')
        prefix = base.path.rstrip('/') + '/'
        if parsed.scheme not in ('http', 'https') or parsed.netloc != base.netloc or not parsed.path.startswith(prefix):
            return None
        key = parsed.path[len(prefix):]
        return key if KEY_PATTERN.fullmatch(key) else None
    except ValueError:
        return None


def extract_keys(values):
    return {key for value in values if isinstance(value, str)
            for url in URL_PATTERN.findall(value) for key in [managed_key(url)] if key}


def content_keys(item):
    kind = getattr(item, '__tablename__', '')
    if kind == 'users':
        # Guest seeds and generated identicons are not storage objects.
        return {item.avatar} if not item.is_guest and item.avatar and KEY_PATTERN.fullmatch(item.avatar) else set()
    return extract_keys([getattr(item, field, None) for field in TRACKED_FIELDS.get(kind, ())])


def prepare_image(data):
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError('每张图片最大 5 MB')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data), formats=['JPEG', 'PNG', 'WEBP']) as source:
                if source.width * source.height > MAX_IMAGE_PIXELS or max(source.size) > 12000:
                    raise ValueError('图片尺寸过大，最多 2400 万像素，最长边 12000 像素')
                if getattr(source, 'n_frames', 1) != 1:
                    raise ValueError('请上传静态 JPG、PNG 或 WebP 图片')
                source.verify()
            with Image.open(io.BytesIO(data), formats=['JPEG', 'PNG', 'WEBP']) as source:
                source.load()
                picture = ImageOps.exif_transpose(source)
                mode = 'RGBA' if 'A' in picture.getbands() or 'transparency' in picture.info else 'RGB'
                # Rebuild pixels, dropping EXIF, XMP, embedded scripts and trailing bytes.
                clean = Image.new(mode, picture.size)
                clean.paste(picture.convert(mode))
                output = io.BytesIO()
                extension, mime = ('png', 'image/png') if mode == 'RGBA' else ('jpg', 'image/jpeg')
                clean.save(output, format='PNG' if mode == 'RGBA' else 'JPEG', **({} if mode == 'RGBA' else {'quality': 90}))
                if output.tell() > MAX_IMAGE_BYTES:
                    # Metadata stripping can expand a compact JPEG or transparent WebP.
                    # Preserve alpha and optimize the cleaned copy instead of rejecting it.
                    extension, mime = 'webp', 'image/webp'
                    output = io.BytesIO(); clean.save(output, 'WEBP', quality=90)
                    while output.tell() > MAX_IMAGE_BYTES and max(clean.size) > 1:
                        clean = clean.resize((max(1, int(clean.width * .8)), max(1, int(clean.height * .8))), Image.Resampling.LANCZOS)
                        output = io.BytesIO(); clean.save(output, 'WEBP', quality=90)
                return output.getvalue(), extension, mime, clean.width, clean.height
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValueError('图片无效，请上传完整的静态 JPG、PNG 或 WebP 图片')


def put_image(key, data, mime):
    from qiniu import put_data
    from .qiniu import get_token
    result, info = put_data(get_token(key, max_size=MAX_IMAGE_BYTES, mime_limit=mime), key, data, mime_type=mime)
    if not result or info.status_code != 200 or result.get('key') != key:
        raise RuntimeError('Image upload failed')


def delete_image(key):
    from qiniu import Auth, BucketManager
    if not KEY_PATTERN.fullmatch(key):
        raise ValueError('Unmanaged object')
    config = current_app.config
    _, info = BucketManager(Auth(config['QI_NIU_ACCESS_KEY'], config['QI_NIU_SECRET_KEY'])).delete(config['QI_NIU_BUCKET'], key)
    if info.status_code not in (200, 612):  # Already absent is a successful cleanup.
        raise RuntimeError('Image deletion failed')


@event.listens_for(Session, 'before_flush')
def collect_changes(session, context, instances):
    if not has_app_context():
        return
    changed = []
    for item in set(session.new) | set(session.dirty) | set(session.deleted):
        kind = getattr(item, '__tablename__', '')
        fields = TRACKED_FIELDS.get(kind)
        if fields:
            changed.append((item, kind, item in session.deleted, content_keys(item)))
    if changed:
        session.info.setdefault('media_changes', []).extend(changed)


@event.listens_for(Session, 'after_flush_postexec')
def update_references(session, context):
    changes = session.info.pop('media_changes', [])
    for item, kind, deleted, keys in changes:
        content_id = str(item.id)
        existing = session.query(MediaReference).filter_by(content_type=kind, content_id=content_id).with_for_update().all()
        prior_ids = {ref.asset_id for ref in existing}
        desired_keys = set() if deleted else keys
        assets = session.query(MediaAsset).filter(or_(MediaAsset.storage_key.in_(desired_keys), MediaAsset.id.in_(prior_ids))).order_by(MediaAsset.id).populate_existing().with_for_update().all() if desired_keys or prior_ids else []
        by_key = {asset.storage_key: asset for asset in assets}
        unknown = desired_keys - set(by_key)
        if unknown and (kind in ('comments', 'messages') or any(key.startswith(IMAGE_PREFIX) for key in unknown)):
            raise MediaReferenceError('图片不存在，请重新上传')
        # Older, unregistered storage URLs stay untouched until an explicit index.
        desired_keys -= unknown
        desired_ids = {by_key[key].id for key in desired_keys}
        for asset in assets:
            if asset.id in desired_ids:
                if asset.status not in ('ready', 'orphan'):
                    raise MediaReferenceError('图片已删除或正在清理，请重新上传')
                if kind in ('comments', 'messages') and asset.owner_id != item.author_id:
                    raise MediaReferenceError('只能使用自己上传的图片')
                if asset.id not in prior_ids:
                    session.add(MediaReference(asset_id=asset.id, content_type=kind, content_id=content_id))
                asset.status, asset.delete_after = 'ready', None
            elif asset.id in prior_ids:
                # Delete is deferred until commit and rechecked against all references.
                for ref in existing:
                    if ref.asset_id == asset.id:
                        session.delete(ref)
                asset.delete_after = datetime.utcnow()
                if has_request_context():
                    g.media_cleanup_needed = True


@event.listens_for(Session, 'after_rollback')
def discard_changes(session):
    session.info.pop('media_changes', None)


def cleanup_images(limit=10, now=None):
    """Claim deletions under a row lock; retries survive process/storage failure.

    Referring transactions lock the same asset before adding a reference, so an
    object being physically deleted can never gain a new reference in parallel.
    """
    now = now or datetime.utcnow()
    ids = [row.id for row in MediaAsset.query.filter(MediaAsset.delete_after <= now,
        MediaAsset.status != 'deleted').order_by(MediaAsset.delete_after).limit(limit).all()]
    deleted = 0
    for asset_id in ids:
        asset = MediaAsset.query.filter_by(id=asset_id).populate_existing().with_for_update().one()
        if not asset.delete_after or asset.delete_after > now or asset.status == 'deleted':
            db.session.commit()
            continue
        if MediaReference.query.filter_by(asset_id=asset_id).with_for_update().first():
            asset.delete_after = None
            db.session.commit()
            continue
        asset.status = 'deleting'
        asset.delete_attempts += 1
        asset.delete_after = now + timedelta(minutes=5)
        key = asset.storage_key
        db.session.commit()
        try:
            delete_image(key)
        except Exception:
            # Keep the retry record; never roll back already-saved user content.
            current_app.logger.warning('Image cleanup deferred for asset %s', asset_id)
            continue
        MediaAsset.query.filter_by(id=asset_id, status='deleting').update({'status': 'deleted', 'delete_after': None})
        db.session.commit()
        deleted += 1
    return deleted
