"""A small text/Markdown allowlist. HTML is never accepted as executable markup."""
import re
from urllib.parse import urlsplit
from .media import managed_key

INLINE = re.compile(r'(!?)\[([^\]\r\n]*)\]\(([^\s()]+)\)')


def safe_link(url):
    if not isinstance(url, str) or re.search(r'[\s\x00-\x1f\x7f<>"\'\\]', url):
        return False
    try:
        parsed = urlsplit(url)
        return parsed.scheme in ('https', 'http') and bool(parsed.hostname) and not parsed.username and not parsed.password
    except ValueError:
        return False


def validate_body(body):
    if not isinstance(body, str) or not body.strip() or len(body) > 5000:
        raise ValueError('请填写 1 至 5000 字的内容')
    # Do not silently clean an attack into different content: reject it.
    if re.search(r'<\s*(?:/?[a-zA-Z]|!|\?)|\x00', body):
        raise ValueError('内容不支持 HTML、脚本或嵌入代码，请使用文字、图片和链接')
    images = 0
    attachments = []
    for match in INLINE.finditer(body):
        image, _, url = match.groups()
        if not safe_link(url):
            raise ValueError('链接仅支持完整的 http 或 https 地址')
        if image:
            images += 1
            attachments.append(match.group(0))
            if not managed_key(url):
                raise ValueError('请使用图片按钮上传图片，不支持外部图片或内嵌图片')
    # Catch dangerous link syntax even when it uses nested parentheses or spaces.
    if re.search(r'\]\(\s*(?:javascript|data|vbscript|file|blob)\s*:', body, re.I):
        raise ValueError('链接仅支持完整的 http 或 https 地址')
    if images > 6:
        raise ValueError('每条内容最多 6 张图片')
    # Text always precedes the ordered attachments; authors cannot change layout.
    text = INLINE.sub(lambda match: '' if match.group(1) else match.group(0), body).rstrip()
    return text + ('\n\n' + '\n'.join(attachments) if attachments else '')
