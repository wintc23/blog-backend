import hmac
import re
import secrets
from datetime import datetime

from flask import jsonify, request
from sqlalchemy.exc import IntegrityError

from . import api
from .. import db
from .. import guest
from ..email_login import (CODE_TTL, SEND_COOLDOWN, MAX_ATTEMPTS,
                           code_digest, email_users, normalize_email, send_login_code)
from ..models import EmailLoginChallenge, Permission, Role, User


def reply(data, status=200):
    response = jsonify(data)
    response.status_code = status
    response.headers['Cache-Control'] = 'no-store'
    return response


def invalid_code():
    # Attempt counters must persist even on a failed verification.
    db.session.commit()
    return reply({'message': '验证码错误或已失效，请检查后重试或重新获取', 'notify': True}, 400)


@api.route('/email-login/code/', methods=['POST'])
def request_email_code():
    data = request.get_json(silent=True)
    email = normalize_email(data.get('email')) if isinstance(data, dict) else None
    if email is None:
        return reply({'message': '请填写正确的邮箱', 'notify': True}, 400)
    limited = guest.consume_limits(guest.client_address(), [
        ('email-send-ip-hour', 20, 3600), ('email-send-ip-day', 100, 86400)])
    if limited is not None:
        return limited
    limited = guest.consume_limits(email, [
        ('email-send-interval', 1, SEND_COOLDOWN), ('email-send-hour', 5, 3600),
        ('email-send-day', 20, 86400)])
    if limited is not None:
        return limited
    now = guest._now()
    # Retain only recent challenges; rate counters have independent lifetimes.
    EmailLoginChallenge.query.filter(EmailLoginChallenge.expires_at < now - 86400).delete(synchronize_session=False)
    users = email_users(email).all()
    if len(users) > 1:
        db.session.commit()
        return reply({'message': '该邮箱账号信息异常，请联系管理员', 'notify': True}, 400)
    user = users[0] if users else None
    EmailLoginChallenge.query.filter_by(email=email, consumed_at=None).update(
        {'consumed_at': now}, synchronize_session=False)
    challenge_id = secrets.token_hex(24)
    code = '{:06d}'.format(secrets.randbelow(1000000))
    challenge = EmailLoginChallenge(id=challenge_id, email=email,
        code_digest=code_digest(challenge_id, email, code), user_id=user.id if user else None,
        email_version=(user.email_version or 0) if user else 0, expires_at=now + CODE_TTL)
    db.session.add(challenge)
    db.session.commit()
    # Release database locks before contacting the mail server.
    try:
        send_login_code(email, code)
    except Exception:
        db.session.rollback()
        EmailLoginChallenge.query.filter_by(id=challenge_id).update({'consumed_at': guest._now()})
        db.session.commit()
        response = reply({'message': '验证码邮件发送失败，请稍后重试', 'notify': True,
                          'retry_after': SEND_COOLDOWN}, 503)
        response.headers['Retry-After'] = str(SEND_COOLDOWN)
        return response
    ready = EmailLoginChallenge.query.filter_by(id=challenge_id, consumed_at=None).filter(
        EmailLoginChallenge.expires_at > guest._now()).update({'ready': True})
    db.session.commit()
    if not ready:
        return invalid_code()
    return reply({'challenge_id': challenge_id, 'expires_in': CODE_TTL,
                  'retry_after': SEND_COOLDOWN, 'message': '验证码已发送，请查收邮箱'})


@api.route('/email-login/', methods=['POST'])
def login_with_email():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return reply({'message': '请填写邮箱和 6 位验证码', 'notify': True}, 400)
    email = normalize_email(data.get('email'))
    code, challenge_id = data.get('code'), data.get('challenge_id')
    if (email is None or not isinstance(code, str) or not re.fullmatch(r'[0-9]{6}', code)
            or not isinstance(challenge_id, str) or not re.fullmatch(r'[a-f0-9]{48}', challenge_id)):
        return reply({'message': '请填写邮箱和 6 位验证码，并先获取验证码', 'notify': True}, 400)
    limited = guest.consume_limits(guest.client_address(), [('email-verify-ip', 100, 3600)])
    if limited is not None:
        return limited
    limited = guest.consume_limits(email, [('email-verify-hour', 30, 3600)])
    if limited is not None:
        return limited
    now = guest._now()
    challenge = EmailLoginChallenge.query.filter_by(id=challenge_id).populate_existing().with_for_update().first()
    if (not challenge or challenge.email != email or not challenge.ready
            or challenge.consumed_at is not None or challenge.expires_at <= now
            or challenge.attempts >= MAX_ATTEMPTS):
        return invalid_code()
    challenge.attempts += 1
    if not hmac.compare_digest(challenge.code_digest, code_digest(challenge_id, email, code)):
        if challenge.attempts >= MAX_ATTEMPTS:
            challenge.consumed_at = now
        return invalid_code()
    users = email_users(email).populate_existing().with_for_update().all()
    user = users[0] if len(users) == 1 else None
    # Binding the code to the saved email version prevents an old code from
    # signing into a changed, deleted/recreated, or newly claimed account.
    if ((challenge.user_id is not None and
         (user is None or user.id != challenge.user_id or user.email_version != challenge.email_version))
            or (challenge.user_id is None and users)):
        challenge.consumed_at = now
        return invalid_code()
    if user is None or user.is_guest:
        role = Role.query.filter_by(name='User').first()
        if role is None or role.permissions != (Permission.FOLLOW | Permission.COMMENT):
            db.session.commit()
            return reply({'message': '邮箱登录暂不可用，请稍后再试', 'notify': True}, 503)
        if user is None:
            username = '用户{:08d}'.format(secrets.randbelow(100000000))
            while User.query.filter_by(username=username).first():
                username = '用户{:08d}'.format(secrets.randbelow(100000000))
            user = User(id_string='email:' + secrets.token_hex(16), email=email,
                        username=username, avatar='generated:' + secrets.token_hex(16), role=role)
            db.session.add(user)
        else:
            # Keep the user id and every relationship, while revoking guest tokens.
            if not user.avatar.startswith('generated:'):
                user.avatar = 'generated:' + user.avatar
            user.id_string = 'email:' + secrets.token_hex(16)
            user.role = role
            user.auth_version = (user.auth_version or 0) + 1
    user.email_verified_at = datetime.utcnow()
    user.last_seen = datetime.utcnow()
    challenge.consumed_at = now
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return reply({'message': '邮箱信息已变更，请重新获取验证码', 'notify': True}, 400)
    return reply({'token': user.generate_auth_token(3600 * 24 * 30), 'user': user.get_detail()})
