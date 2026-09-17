"""Bounded HTTPS requests. No redirects carrying provider credentials."""
import ipaddress
import os
import socket
import time
from urllib.parse import urlsplit, urljoin
import requests
from .configuration import GenerationError


def validate_url(url, resolve=True):
    if not isinstance(url, str) or len(url) > 2048:
        raise ValueError('URL 不正确')
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError('只允许不含凭据和片段的 HTTPS URL')
    if parsed.port not in (None, 443):
        raise ValueError('只允许 HTTPS 默认端口')
    if parsed.hostname.lower() in ('localhost',) or parsed.hostname.endswith(('.local', '.internal')):
        raise ValueError('不能访问本机或内网地址')
    try:
        literal = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        literal = None
    addresses = [literal] if literal else []
    if resolve and not literal:
        try:
            addresses = [ipaddress.ip_address(row[4][0]) for row in socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)]
        except OSError:
            raise GenerationError('dns_error', '无法解析来源或模型服务地址', True)
    if any(not ip.is_global for ip in addresses):
        raise ValueError('不能访问本机或内网地址')
    return url


def fetch(url, method='GET', payload=None, headers=None, timeout=30, limit=3 * 1024 * 1024):
    return _fetch(url, method, payload, headers, timeout, limit, validate_url)


def cpa_image_url():
    """Only the deployment environment can select this local image service."""
    base = os.environ.get('CONTENT_CPA_BASE_URL', 'http://127.0.0.1:8317/v1').rstrip('/')
    parsed = urlsplit(base)
    if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1'
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path != '/v1' or not parsed.port):
        raise ValueError('CONTENT_CPA_BASE_URL 必须是 http://127.0.0.1:端口/v1')
    return base + '/images/generations'


def fetch_cpa_image(payload, headers, timeout, limit):
    url = cpa_image_url()
    def validate_local(target):
        if target != url:
            raise ValueError('CPA 只能访问已配置的本机图片接口')
    return _fetch(url, 'POST', payload, headers, timeout, limit, validate_local)


def _fetch(url, method, payload, headers, timeout, limit, validator):
    started = time.monotonic()
    session = requests.Session()
    session.trust_env = False
    try:
        for redirect in range(4):
            validator(url)
            with session.request(method, url, json=payload, headers=headers, timeout=(10, timeout),
                                 stream=True, allow_redirects=False) as response:
                if response.is_redirect:
                    if method != 'GET' or headers and 'Authorization' in headers:
                        raise GenerationError('provider_redirect', '模型服务地址发生重定向，请检查 API 地址')
                    url = urljoin(url, response.headers.get('Location', ''))
                    continue
                if response.status_code >= 400:
                    retry = response.status_code == 429 or response.status_code >= 500
                    raise GenerationError('http_{}'.format(response.status_code), '外部服务返回 HTTP {}'.format(response.status_code), retry)
                if int(response.headers.get('Content-Length', '0')) > limit:
                    raise GenerationError('response_too_large', '外部服务返回内容过大')
                data = bytearray()
                for chunk in response.iter_content(65536):
                    data.extend(chunk)
                    if len(data) > limit or time.monotonic() - started > timeout:
                        raise GenerationError('response_limit', '外部请求超过大小或时间限制', True)
                return bytes(data), {key.lower(): value for key, value in response.headers.items()}
        raise GenerationError('redirect_limit', '来源重定向次数过多')
    except requests.RequestException:
        raise GenerationError('network_error', '外部服务连接失败或超时', True)
    finally:
        session.close()
