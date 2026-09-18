import io
import warnings
from pathlib import Path
from uuid import uuid4
from PIL import Image, ImageOps, UnidentifiedImageError
from .. import db
from . import cloud
from .policy import upload_policy, HARD_BYTES, HARD_PIXELS, MAX_EDGE
from ..image_tool_models import ImageToolAsset

MAX_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 40_000_000


def key(asset, original=False):
    return cloud.PREFIX + asset.id + ('.original' if original else '.png')


def read(asset, original=False):
    return cloud.read(key(asset, original), HARD_BYTES if original else 80 * 1024 * 1024)


def store(task_id, data, filename, kind='input', position=0, asset_id=None, original_uploaded=False, policy=None):
    policy = policy or upload_policy()
    max_bytes = min(policy['max_bytes'], HARD_BYTES) if kind == 'input' else MAX_BYTES
    max_pixels = min(policy['max_pixels'], HARD_PIXELS) if kind == 'input' else MAX_PIXELS
    if not data or len(data) > max_bytes:
        raise ValueError('图片超过上传配置，请重新选择以自动优化')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data), formats=['JPEG', 'PNG', 'WEBP']) as source:
                if source.width * source.height > max_pixels or max(source.size) > MAX_EDGE or getattr(source, 'n_frames', 1) != 1:
                    raise ValueError('图片尺寸超过上传配置或不是静态图片，请重新选择以自动优化')
                extension = {'JPEG': 'jpg', 'PNG': 'png', 'WEBP': 'webp'}[source.format]
                edge = min(policy['processing_max_edge'], 4096)
                if kind == 'input':
                    source.draft(source.mode, (edge, edge))
                source.load()
                ImageOps.exif_transpose(source, in_place=True)
                if kind == 'input':
                    source.thumbnail((edge, edge), Image.Resampling.LANCZOS)
                picture = source.convert('RGBA' if 'A' in source.getbands() or 'transparency' in source.info else 'RGB')
                clean = Image.new(picture.mode, picture.size)
                clean.paste(picture)
                output = io.BytesIO()
                clean.save(output, 'PNG')
                # Input copies sent to the model are also bounded by encoded bytes.
                while kind == 'input' and output.tell() > MAX_BYTES and min(clean.size) > 1:
                    clean = clean.resize((max(1, int(clean.width * .8)), max(1, int(clean.height * .8))), Image.Resampling.LANCZOS)
                    output = io.BytesIO(); clean.save(output, 'PNG')
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
