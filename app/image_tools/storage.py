import io
import warnings
from pathlib import Path
from uuid import uuid4
from PIL import Image, ImageOps, UnidentifiedImageError
from .. import db
from . import cloud
from ..image_tool_models import ImageToolAsset

MAX_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 40_000_000


def key(asset, original=False):
    return cloud.PREFIX + asset.id + ('.original' if original else '.png')


def read(asset, original=False):
    return cloud.read(key(asset, original), MAX_BYTES if original else 80 * 1024 * 1024)


def store(task_id, data, filename, kind='input', position=0, asset_id=None, original_uploaded=False):
    if not data or len(data) > MAX_BYTES:
        raise ValueError('每张图片最大 20 MB')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data), formats=['JPEG', 'PNG', 'WEBP']) as source:
                if source.width * source.height > MAX_PIXELS or max(source.size) > 16000 or getattr(source, 'n_frames', 1) != 1:
                    raise ValueError('请上传不超过 4000 万像素的静态图片')
                source.load()
                extension = {'JPEG': 'jpg', 'PNG': 'png', 'WEBP': 'webp'}[source.format]
                picture = ImageOps.exif_transpose(source).convert('RGBA' if 'A' in source.getbands() else 'RGB')
                clean = Image.new(picture.mode, picture.size)
                clean.paste(picture)
                output = io.BytesIO()
                clean.save(output, 'PNG')
        if output.tell() > 80 * 1024 * 1024:
            raise ValueError('图片解码后过大，请降低分辨率')
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValueError('图片无效，请上传 JPG、PNG 或 WebP 图片')
    name = Path(filename.replace('\\', '/')).name
    name = ''.join(c for c in name if c.isprintable() and c not in '/\\').strip()[:160] or '图片'
    asset = ImageToolAsset(id=asset_id or uuid4().hex, task_id=task_id, kind=kind, name=name, extension=extension,
        width=clean.width, height=clean.height, byte_size=len(data), position=position)
    if not original_uploaded:
        cloud.put(key(asset, True), data, 'application/octet-stream')
    cloud.put(key(asset), output.getvalue())
    db.session.add(asset)
    return asset


def discard(asset):
    for name in (key(asset), key(asset, True)):
        cloud.delete(name)
