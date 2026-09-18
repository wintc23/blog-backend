"""Shared generation/edit adapter, using the existing server-managed CPA service."""
import base64
import json
import os
import time
import requests
from .storage import read, MAX_BYTES
from ..generation.network import cpa_image_url, fetch


IMAGE_MODEL = 'gpt-image-2.5-flare'


def ready():
    try:
        cpa_image_url()
    except ValueError:
        return False
    return bool(os.environ.get('CONTENT_CPA_API_KEY'))


def generate(prompt, ratio, source, request_id):
    if not ready():
        raise RuntimeError('图片服务尚未配置')
    size = {'1:1': '1024x1024', '3:2': '1536x1024', '2:3': '1024x1536'}.get(ratio)
    if not size:
        size = '1024x1024' if not source or 0.85 <= source.width / source.height <= 1.18 else ('1536x1024' if source.width > source.height else '1024x1536')
    model = IMAGE_MODEL  # Every tool uses Image 2.5; never fall back to another model.
    payload = dict(model=model, prompt=prompt, n=1, size=size, output_format='png')
    url = cpa_image_url()
    options = {'json': payload}
    if source:
        url = url.rsplit('/', 1)[0] + '/edits'
        options = dict(data=payload, files={'image': ('reference.png', read(source), 'image/png')})
    started = time.monotonic()
    with requests.Session() as client:
        client.trust_env = False
        with client.post(url, headers={'Authorization': 'Bearer ' + os.environ['CONTENT_CPA_API_KEY'], 'X-Client-Request-Id': request_id},
                         timeout=(10, 600), allow_redirects=False, stream=True, **options) as response:
            if response.status_code != 200:
                raise RuntimeError('图片服务返回 HTTP {}'.format(response.status_code))
            content = bytearray()
            for chunk in response.iter_content(65536):
                content.extend(chunk)
                if len(content) > 30 * 1024 * 1024 or time.monotonic() - started > 600:
                    raise TimeoutError('图片服务超时，结果待确认')
    try:
        value = json.loads(content)
        returned = value.get('model')
        if returned and returned != model and not returned.startswith(model + '-'):
            raise ValueError('model mismatch')
        result = value['data'][0]
        data = base64.b64decode(result['b64_json'], validate=True) if result.get('b64_json') else fetch(result['url'], timeout=60, limit=MAX_BYTES)[0]
        if len(data) > MAX_BYTES:
            raise ValueError('image too large')
        return data
    except (ValueError, KeyError, IndexError, TypeError):
        raise RuntimeError('图片服务未返回有效图片')
