"""Private Qiniu objects. No filesystem fallback or website-domain dependency."""
import hashlib
import hmac
import os
import re
from datetime import datetime
from urllib.parse import quote
import requests
from flask import current_app
from qiniu import Auth, BucketManager

PREFIX = 'image-tools/'


class StorageError(ValueError):
    status = 503


def setting(name):
    return current_app.config.get(name) or os.environ.get(name)


def credentials():
    bucket = setting('IMAGE_TOOLS_QINIU_BUCKET')
    region = setting('IMAGE_TOOLS_QINIU_REGION')
    if not bucket or not region or not re.fullmatch(r'[a-z0-9-]+', bucket) or not re.fullmatch(r'[a-z0-9-]+', region):
        raise StorageError('图片云存储尚未配置')
    return bucket, region, current_app.config['QI_NIU_ACCESS_KEY'], current_app.config['QI_NIU_SECRET_KEY']


def auth():
    _, _, access, secret = credentials()
    return Auth(access, secret)


def signed_url(key, expires=60, filename=None):
    """AWS SigV4 query signing for Qiniu's HTTPS S3 endpoint (not AWS storage)."""
    bucket, region, access, secret = credentials()
    host = 's3.{}.qiniucs.com'.format(region)
    stamp = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
    date = stamp[:8]
    scope = '{}/{}/s3/aws4_request'.format(date, region)
    params = {'X-Amz-Algorithm': 'AWS4-HMAC-SHA256', 'X-Amz-Credential': access + '/' + scope,
              'X-Amz-Date': stamp, 'X-Amz-Expires': str(expires), 'X-Amz-SignedHeaders': 'host'}
    if filename:
        params['response-content-disposition'] = "attachment; filename*=UTF-8''" + quote(filename, safe='')
    query = '&'.join(quote(k, safe='-_.~') + '=' + quote(v, safe='-_.~') for k, v in sorted(params.items()))
    path = '/' + bucket + '/' + quote(key, safe='/-_.~')
    canonical = '\n'.join(['GET', path, query, 'host:' + host + '\n', 'host', 'UNSIGNED-PAYLOAD'])
    to_sign = '\n'.join(['AWS4-HMAC-SHA256', stamp, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    signing_key = ('AWS4' + secret).encode()
    for part in [date, region, 's3', 'aws4_request']:
        signing_key = hmac.new(signing_key, part.encode(), hashlib.sha256).digest()
    signature = hmac.new(signing_key, to_sign.encode(), hashlib.sha256).hexdigest()
    return 'https://' + host + path + '?' + query + '&X-Amz-Signature=' + signature


def upload_token(key, size, mime=None):
    bucket, _, _, _ = credentials()
    policy = {'insertOnly': 1, 'fsizeLimit': size, 'returnBody': '{"key":$(key),"hash":$(etag)}'}
    if mime:
        policy['mimeLimit'] = mime
    return auth().upload_token(bucket, key, 600, policy)


def put(key, data, mime='image/png'):
    host = setting('IMAGE_TOOLS_QINIU_UPLOAD_URL')
    if not host or not host.startswith('https://'):
        raise StorageError('图片上传服务尚未配置')
    try:
        with requests.Session() as client:
            client.trust_env = False
            response = client.post(host, data={'token': upload_token(key, len(data)), 'key': key},
                                   files={'file': ('image', data, mime)}, timeout=(10, 120), allow_redirects=False)
        if response.status_code != 200 or response.json().get('key') != key:
            raise StorageError('图片上传云存储失败，请稍后重试')
    except (requests.RequestException, ValueError):
        raise StorageError('图片上传云存储失败，请稍后重试') from None


def read(key, limit=80 * 1024 * 1024):
    try:
        with requests.Session() as client:
            client.trust_env = False
            with client.get(signed_url(key), timeout=(10, 90), stream=True, allow_redirects=False) as response:
                if response.status_code != 200:
                    raise StorageError('云端图片暂不可用，请稍后重试')
                data = bytearray()
                for chunk in response.iter_content(65536):
                    data.extend(chunk)
                    if len(data) > limit:
                        raise ValueError('图片文件过大')
                return bytes(data)
    except requests.RequestException:
        raise StorageError('图片云存储连接失败，请稍后重试') from None


def delete(key):
    bucket, _, _, _ = credentials()
    _, info = BucketManager(auth()).delete(bucket, key)
    if info.status_code not in (200, 612):
        raise StorageError('云端图片删除失败，请稍后重试')


def objects():
    bucket, _, _, _ = credentials()
    manager, marker = BucketManager(auth()), None
    while True:
        result, eof, info = manager.list(bucket, prefix=PREFIX, marker=marker, limit=1000)
        if info.status_code != 200:
            raise StorageError('云存储清理暂不可用')
        yield from result.get('items', [])
        if eof: break
        marker = result['marker']
