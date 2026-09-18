"""OpenAI-compatible HTTP adapters; credentials never enter job snapshots."""
import base64
import hashlib
import json
import os
import struct
import zlib
from pathlib import Path
from .configuration import GenerationError
from .network import fetch, fetch_cpa_image, cpa_image_url


def readiness(config, reuse_cover=False):
    missing = []
    for name in ('text_model', 'image_model'):
        model = config[name]
        if name == 'image_model' and reuse_cover:
            continue
        if model.get('provider') == 'codex':
            from .codex_provider import executable
            if not executable():
                missing.append('CONTENT_CODEX_BIN')
            if name == 'image_model':
                from .codex_provider import image_login_ready
                if not image_login_ready():
                    missing.append('CONTENT_IMAGE_CODEX_LOGIN')
            continue
        if model.get('provider') == 'cpa':
            try:
                cpa_image_url()
            except ValueError:
                missing.append('CONTENT_CPA_BASE_URL')
        for field in (('model',) if model.get('provider') == 'cpa' else ('base_url', 'model')):
            if not model[field]:
                missing.append(name + '.' + field)
        if not os.environ.get(model['credential_ref']):
            missing.append(model['credential_ref'])
    for name in (() if reuse_cover else ('QI_NIU_ACCESS_KEY', 'QI_NIU_SECRET_KEY', 'QI_NIU_BUCKET', 'QI_NIU_LINK_URL')):
        from flask import current_app
        if not current_app.config.get(name):
            missing.append(name)
    return missing


def call(model, endpoint, payload, request_key, limit=2 * 1024 * 1024):
    key = os.environ.get(model['credential_ref'])
    is_cpa = model.get('provider') == 'cpa'
    if not key or not model['model'] or (not is_cpa and not model['base_url']):
        raise GenerationError('provider_not_configured', '模型服务未配置完整，请检查模型、API 地址和凭据引用')
    request_headers = {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json', 'X-Client-Request-Id': request_key}
    payload = dict(payload, model=model['model'])
    if is_cpa:
        if endpoint != '/images/generations' or model['base_url']:
            raise GenerationError('provider_not_configured', 'CPA 仅支持服务端配置的图片生成接口')
        try:
            raw, headers = fetch_cpa_image(payload, request_headers, model['timeout'], limit)
        except ValueError:
            raise GenerationError('provider_not_configured', '请检查服务器 CONTENT_CPA_BASE_URL 配置')
    else:
        raw, headers = fetch(model['base_url'].rstrip('/') + endpoint, 'POST', payload, request_headers,
                             timeout=model['timeout'], limit=limit)
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise GenerationError('invalid_provider_response', '模型服务未返回有效 JSON', True)
    if not isinstance(value, dict):
        raise GenerationError('invalid_provider_response', '模型服务返回格式不正确', True)
    returned_model = value.get('model')
    if is_cpa and returned_model and returned_model != model['model'] and not str(returned_model).startswith(model['model'] + '-'):
        raise GenerationError('image_model_mismatch', '图片服务返回的模型与任务配置不一致')
    metadata = {'model': model['model'], 'request_id': headers.get('x-request-id'), 'client_request_id': request_key,
                'response_id': value.get('id'), 'usage': value.get('usage'), 'provider': model.get('provider', 'openai_compatible')}
    if returned_model:
        metadata['returned_model'] = returned_model
    return value, metadata


def generate_text(config, system, inputs, request_key):
    if config['text_model'].get('provider') == 'codex':
        from .codex_provider import generate
        return generate(config['text_model'], system, inputs, request_key)
    value, metadata = call(config['text_model'], '/chat/completions', {
        'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': json.dumps(inputs, ensure_ascii=False)}],
        'response_format': {'type': 'json_object'},
    }, request_key)
    try:
        choice = value['choices'][0]
        if choice.get('finish_reason') not in (None, 'stop'):
            raise ValueError('incomplete response')
        document = json.loads(choice['message']['content'])
        if not isinstance(document, dict):
            raise ValueError('not an object')
    except (KeyError, IndexError, TypeError, ValueError):
        raise GenerationError('invalid_model_json', '正文输出不完整或不符合 JSON 格式', True)
    return document, metadata


def png_dimensions(data):
    if not data.startswith(b'\x89PNG\r\n\x1a\n') or len(data) > 20 * 1024 * 1024:
        raise GenerationError('invalid_image', '图片必须是小于 20 MB 的 PNG 文件')
    offset, width, height, ended, has_pixels = 8, 0, 0, False, False
    while offset + 12 <= len(data):
        length = struct.unpack('>I', data[offset:offset + 4])[0]
        kind = data[offset + 4:offset + 8]
        end = offset + 8 + length
        if end + 4 > len(data) or zlib.crc32(data[offset + 4:end]) & 0xffffffff != struct.unpack('>I', data[end:end + 4])[0]:
            raise GenerationError('invalid_image', 'PNG 文件损坏')
        if offset == 8:
            if kind != b'IHDR' or length != 13:
                raise GenerationError('invalid_image', 'PNG 文件头不正确')
            width, height = struct.unpack('>II', data[offset + 8:offset + 16])
        if kind == b'IDAT' and length:
            has_pixels = True
        if kind == b'IEND':
            ended = True
            break
        offset = end + 4
    if not ended or not has_pixels or not 256 <= width <= 8192 or not 128 <= height <= 8192 or width * height > 32_000_000:
        raise GenerationError('invalid_image', '图片尺寸不正确或文件不完整')
    return width, height


def generate_image(config, prompt, request_key):
    model = config['image_model']
    if model.get('provider') == 'codex':
        from .codex_provider import generate_image as codex_image
        data, metadata = codex_image(model, prompt, request_key)
        return save_image(data, request_key), metadata
    payload = {'prompt': prompt, 'n': 1, 'size': model['size']}
    if model.get('provider') == 'cpa':
        payload['output_format'] = 'png'
    if model['response_format'] != 'auto':
        payload['response_format'] = model['response_format']
    value, metadata = call(model, '/images/generations', payload, request_key, limit=30 * 1024 * 1024)
    try:
        result = value['data'][0]
        if result.get('b64_json'):
            data = base64.b64decode(result['b64_json'], validate=True)
        elif result.get('url'):
            data, _ = fetch(result['url'], limit=20 * 1024 * 1024, timeout=60)
        else:
            raise ValueError('missing image')
    except (ValueError, KeyError, IndexError, TypeError):
        raise GenerationError('invalid_image_response', '图片服务未返回有效图片', True)
    return save_image(data, request_key), metadata


def save_image(data, request_key):
    width, height = png_dimensions(data)
    digest = hashlib.sha256(data).hexdigest()
    from ..image_tools import cloud
    cloud.put('generation-artifacts/' + digest + '.png', data)
    return {'filename': digest + '.png', 'sha256': digest, 'width': width, 'height': height}


def upload_image(asset):
    from qiniu import put_data
    from flask import current_app
    from ..qiniu import get_token
    from .. import db
    from ..media_models import MediaAsset
    from datetime import datetime, timedelta
    from uuid import uuid4
    from ..image_tools import cloud
    data = cloud.read('generation-artifacts/' + asset['sha256'] + '.png', 20 * 1024 * 1024)
    if hashlib.sha256(data).hexdigest() != asset['sha256']:
        raise GenerationError('asset_corrupt', '已生成图片校验失败')
    asset_id = uuid4().hex
    key = 'managed-images/' + asset_id + '.png'
    url = current_app.config['QI_NIU_LINK_URL'].rstrip('/') + '/' + key
    record = MediaAsset(id=asset_id, storage_key=key, url=url, mime_type='image/png',
        byte_size=len(data), width=asset['width'], height=asset['height'],
        delete_after=datetime.utcnow() + timedelta(hours=24))
    db.session.add(record)
    db.session.commit()
    result, info = put_data(get_token(key, max_size=20 * 1024 * 1024, mime_limit='image/png'), key, data, mime_type='image/png')
    if not result or info.status_code != 200:
        raise GenerationError('upload_failed', '图片上传七牛失败', True)
    record.status = 'ready'
    db.session.commit()
    return {'url': url,
            'width': asset['width'], 'height': asset['height'], 'sha256': asset['sha256'],
            'credit': 'AI 生成概念插图', 'rights': 'ai_generated'}
