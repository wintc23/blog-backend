"""Image assets are addressed by database IDs, never model-supplied paths."""
import base64
import hashlib
import imghdr
import os
import time
from pathlib import Path


class Media:
    def __init__(self, store, app):
        self.store, self.app = store, app

    def save(self, session, data):
        if len(data) > 20 * 1024 * 1024:
            raise ValueError('图片不能超过 20 MB')
        kind = imghdr.what(None, data)
        if kind not in ('png', 'jpeg', 'webp'):
            raise ValueError('当前支持 PNG、JPEG 和 WebP 图片')
        asset_id = hashlib.sha256(data).hexdigest()
        folder = self.store.root / 'assets'
        folder.mkdir(exist_ok=True, mode=0o700)
        path = folder / (asset_id + '.' + kind)
        path.write_bytes(data)
        # Include session hash in the ID to prevent cross-conversation access.
        scoped_id = hashlib.sha256((session + asset_id).encode()).hexdigest()[:32]
        self.store.add_asset(scoped_id, session, path)
        return {'asset_id': scoped_id, 'format': kind}

    def get(self, session, asset_id):
        asset = next((a for a in self.store.assets(session) if a['id'] == asset_id), None)
        if not asset:
            raise ValueError('找不到当前会话的图片，请重新发送')
        path = Path(asset['path']).resolve()
        if path.parent != (self.store.root / 'assets').resolve() or not path.is_file() or path.is_symlink():
            raise ValueError('图片文件不可用')
        return asset, path

    def upload(self, session, asset_id):
        from qiniu import put_file
        from app.qiniu import get_token
        asset, path = self.get(session, asset_id)
        if asset['url']:
            return {'asset_id': asset_id, 'url': asset['url']}
        key = 'lark-assets/' + path.name
        with self.app.app_context():
            result, info = put_file(get_token(key), key, str(path), mime_type='image/' + path.suffix[1:])
            if not result or info.status_code != 200:
                raise RuntimeError('图片上传失败，原图已保留，可重试上传。')
            url = self.app.config['QI_NIU_LINK_URL'].rstrip('/') + '/' + key
        self.store.add_asset(asset_id, session, path, url)
        return {'asset_id': asset_id, 'url': url}

    def generate(self, session, prompt, request_id, asset_id=None):
        import requests
        from app.generation.network import cpa_image_url, fetch
        from app.generation.providers import png_dimensions
        if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 16000:
            raise ValueError('请提供有效的图片要求')
        key = os.environ.get('CONTENT_CPA_API_KEY')
        if not key:
            raise RuntimeError('图片服务尚未配置')
        model = os.environ.get('LARK_BRIDGE_IMAGE_MODEL', 'gpt-image-2.5-flare')
        payload = {'model': model, 'prompt': prompt, 'n': 1, 'size': '1536x1024', 'output_format': 'png'}
        url = cpa_image_url()
        files = None
        if asset_id:
            _, path = self.get(session, asset_id)
            files = {'image': (path.name, path.read_bytes(), 'image/' + path.suffix[1:])}
            url = url.rsplit('/', 1)[0] + '/edits'
        client = requests.Session()
        client.trust_env = False
        started = time.monotonic()
        try:
            options = {'data': payload, 'files': files} if files else {'json': payload}
            with client.post(url, headers={'Authorization': 'Bearer ' + key, 'X-Client-Request-Id': request_id},
                             timeout=(10, 600), allow_redirects=False, stream=True, **options) as response:
                if response.status_code != 200:
                    raise RuntimeError('图片服务返回 HTTP {}，未生成或发布图片。'.format(response.status_code))
                data = bytearray()
                for chunk in response.iter_content(65536):
                    data.extend(chunk)
                    if len(data) > 30 * 1024 * 1024 or time.monotonic() - started > 600:
                        raise RuntimeError('图片服务响应超过限制')
            import json
            value = json.loads(data)
            returned_model = value.get('model')
            if returned_model and returned_model != model and not returned_model.startswith(model + '-'):
                raise RuntimeError('图片服务返回了非指定模型')
            result = value['data'][0]
            if result.get('b64_json'):
                raw = base64.b64decode(result['b64_json'], validate=True)
            else:
                raw, _ = fetch(result['url'], limit=20 * 1024 * 1024, timeout=60)
            png_dimensions(raw)
            return dict(self.save(session, raw), model=model, operation='edit' if asset_id else 'generate')
        except requests.RequestException:
            raise RuntimeError('图片服务连接失败或超时；未自动重试，请查询任务后再继续。')
        except (ValueError, KeyError, IndexError, TypeError):
            raise RuntimeError('图片服务返回了无效结果；没有发布图片，也不会自动重试。')
        finally:
            client.close()
