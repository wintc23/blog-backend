"""Guest identities and database-backed limits shared by every worker."""
import hashlib
import hmac
import ipaddress
import secrets
import time
from urllib.parse import quote

from flask import current_app, jsonify, request
from sqlalchemy.dialects.mysql import insert as mysql_insert

from . import db
from .models import GuestRateLimit


def _now():
    return int(time.time())


def avatar_url(seed):
    # The public avatar seed is separate from the signed login credential.
    bits = hashlib.sha256(str(seed).encode('utf-8')).digest()
    palettes = [('#e8efff', '#496dc8'), ('#e1f3eb', '#36866c'),
                ('#fff0dc', '#b77630'), ('#f2e9ff', '#8863b5'),
                ('#ffe8ec', '#bc6579'), ('#dff2f5', '#357e91')]
    background, foreground = palettes[bits[0] % len(palettes)]
    shapes = []
    for y in range(5):
        for x in range(3):
            if bits[1 + y * 3 + x] % 2 == 0 or (x == 2 and y == 2):
                for column in ({x, 4 - x}):
                    shapes.append('<rect x="{}" y="{}" width="9" height="9" rx="2"/>'.format(
                        8 + column * 10, 8 + y * 10))
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">'
           '<rect width="64" height="64" rx="32" fill="{}"/><g fill="{}">{}</g></svg>').format(
               background, foreground, ''.join(shapes))
    return 'data:image/svg+xml;charset=utf-8,' + quote(svg, safe='')


def client_address():
    """Only the local reverse proxy may supply X-Real-IP; ignore arbitrary XFF."""
    remote = request.remote_addr or 'unknown'
    try:
        address = ipaddress.ip_address(remote)
        if address.is_loopback and request.headers.get('X-Real-IP'):
            address = ipaddress.ip_address(request.headers['X-Real-IP'])
        # Rotating IPv6 privacy addresses in one network share a limit.
        if address.version == 6:
            return str(ipaddress.ip_network(str(address) + '/64', strict=False))
        return str(address)
    except ValueError:
        return remote


def consume_limits(subject, limits, amount=1):
    """Reserve slots in the caller's transaction, rolling all back on rejection.

    Each (scope, maximum, seconds) keeps one fixed-window row per subject.
    Locking reads and a unique key serialize concurrent requests on MySQL.
    """
    if not isinstance(amount, int) or amount < 1:
        raise ValueError('Limit reservation must be a positive integer')
    now = _now()
    secret = str(current_app.config['SECRET_KEY']).encode('utf-8')
    # Expired counters are expendable. Occasional cleanup bounds storage without
    # requiring another scheduled service or delaying every request.
    if secrets.randbelow(100) == 0:
        GuestRateLimit.query.filter(GuestRateLimit.expires_at < now - 86400).delete(synchronize_session=False)
    for scope, maximum, seconds in limits:
        key = hmac.new(secret, (scope + ':' + subject).encode('utf-8'), hashlib.sha256).hexdigest()
        values = dict(key=key, hits=0, expires_at=now + seconds)
        if db.engine.dialect.name == 'mysql':
            statement = mysql_insert(GuestRateLimit.__table__).values(**values)
            statement = statement.on_duplicate_key_update(key=statement.inserted.key)
        else:
            # SQLite is used by the isolated test suite. The write takes its
            # transaction lock before the read, also serializing parallel calls.
            statement = GuestRateLimit.__table__.insert().prefix_with('OR IGNORE').values(**values)
        db.session.execute(statement)
        row = GuestRateLimit.query.filter_by(key=key).populate_existing().with_for_update().one()
        if row.expires_at <= now:
            row.hits = 0
            row.expires_at = now + seconds
        if row.hits + amount > maximum:
            retry_after = max(1, row.expires_at - now)
            db.session.rollback()
            response = jsonify({'message': '操作太频繁，请稍后再试', 'notify': True,
                                'retry_after': retry_after})
            response.status_code = 429
            response.headers['Retry-After'] = str(retry_after)
            return response
        row.hits += amount
        db.session.flush()
    return None


def limit_guest_post(user, body):
    if not user.is_guest:
        return None
    if not isinstance(body, str) or not body.strip() or len(body) > 2000:
        return jsonify({'message': '请填写 1 至 2000 字的内容', 'notify': True}), 400
    return consume_limits(str(user.id), [('post-interval', 1, 15), ('post-hour', 10, 3600)])
