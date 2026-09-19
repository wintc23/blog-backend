import hashlib
import secrets
from datetime import datetime, timedelta
from flask import g, jsonify, request
from . import api
from .decorators import login_required
from .. import db
from ..album_models import DeviceLogin
from ..models import User


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def valid_secret(value):
    return isinstance(value, str) and 32 <= len(value) <= 128


def response(data, status=200):
    result = jsonify(data)
    result.headers['Cache-Control'] = 'no-store'
    return result, status


@api.route('/device-logins/', methods=['POST'])
@login_required
def create_device_login():
    if g.current_user.is_guest:
        return response({'message': '请先使用正式账号登录'}, 403)
    now = datetime.utcnow()
    DeviceLogin.query.filter(DeviceLogin.expires_at < now - timedelta(days=1)).delete()
    # Creating a new QR revokes previous unconsumed codes for this account.
    User.query.filter_by(id=g.current_user.id).with_for_update().first()
    DeviceLogin.query.filter(DeviceLogin.owner_id == g.current_user.id, DeviceLogin.status.in_(['waiting', 'scanned', 'approved'])).update({'status': 'cancelled'}, synchronize_session=False)
    secret = secrets.token_urlsafe(32)
    session = DeviceLogin(id=secrets.token_hex(16), owner_id=g.current_user.id, auth_version=g.current_user.auth_version or 0,
        scan_hash=digest(secret), expires_at=now + timedelta(minutes=5))
    db.session.add(session); db.session.commit()
    return response({'id': session.id, 'scan_token': secret, 'expires_in': 300}, 201)


@api.route('/device-logins/<session_id>/', methods=['GET', 'DELETE', 'PATCH'])
@login_required
def manage_device_login(session_id):
    session = DeviceLogin.query.filter_by(id=session_id, owner_id=g.current_user.id).with_for_update().first()
    if not session:
        return response({'message': '登录请求不存在'}, 404)
    if request.method == 'DELETE':
        session.status = 'cancelled' if session.status != 'consumed' else 'consumed'
    elif request.method == 'PATCH':
        if session.expires_at <= datetime.utcnow() or session.status != 'scanned' or session.auth_version != (g.current_user.auth_version or 0):
            return response({'message': '请求已失效，请重新扫码'}, 409)
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or data.get('code') != session.code:
            return response({'message': '设备确认码不匹配'}, 400)
        session.status = 'approved'
    db.session.commit()
    return response({'status': 'expired' if session.expires_at <= datetime.utcnow() else session.status, 'code': session.code})


@api.route('/device-logins/<session_id>/claim/', methods=['POST'])
@api.route('/device-logins/<session_id>/consume/', methods=['POST'])
def claim_device_login(session_id):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return response({'message': '请求格式不正确'}, 400)
    secret = data.get('client_secret')
    if not valid_secret(secret):
        return response({'message': '设备凭证无效'}, 400)
    session = DeviceLogin.query.filter_by(id=session_id).with_for_update().first()
    owner = User.query.get(session.owner_id) if session else None
    if not session or not owner or session.expires_at <= datetime.utcnow() or session.auth_version != (owner.auth_version or 0) or session.status in ('cancelled', 'consumed'):
        return response({'message': '二维码已失效，请重新扫码'}, 410)
    if request.path.endswith('/claim/'):
        scan = data.get('scan_token')
        if not valid_secret(scan) or not secrets.compare_digest(digest(scan), session.scan_hash):
            return response({'message': '二维码无效'}, 403)
        if session.claim_hash and not secrets.compare_digest(session.claim_hash, digest(secret)):
            return response({'message': '二维码已被另一台设备扫描，请重新生成'}, 409)
        if session.status == 'waiting':
            session.claim_hash = digest(secret)
            session.code = '{:06d}'.format(secrets.randbelow(1000000))
            session.status = 'scanned'
        db.session.commit()
        return response({'status': session.status, 'code': session.code})
    if not session.claim_hash or not secrets.compare_digest(session.claim_hash, digest(secret)):
        return response({'message': '设备凭证无效'}, 403)
    if session.status != 'approved':
        return response({'status': session.status, 'code': session.code})
    # Row lock makes redemption single-use even when two polls race.
    session.status = 'consumed'
    token = owner.generate_auth_token(86400)
    db.session.commit()
    return response({'status': 'consumed', 'token': token, 'user': owner.to_json(), 'redirect': '/manage/moments'})
