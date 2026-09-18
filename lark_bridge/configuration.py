"""Explicit deployment settings; no production domain or machine paths."""
import os
from urllib.parse import urlsplit


def site_url():
    value = os.environ.get('LARK_BRIDGE_SITE_URL', '').strip().rstrip('/')
    if not value:
        return ''
    parsed = urlsplit(value)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or '\\' in value or any(c.isspace() for c in value)):
        raise ValueError('LARK_BRIDGE_SITE_URL 必须是不含账号、查询参数和片段的 HTTP(S) 站点地址')
    parsed.port
    return value


def public_links():
    base = site_url()
    return {'home': base + '/', 'moments': base + '/moments'}
