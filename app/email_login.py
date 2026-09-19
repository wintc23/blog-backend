"""Email login primitives. Verification never stores or logs the plaintext code."""
import hashlib
import hmac
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr
from html import escape

from flask import current_app
from sqlalchemy import func

from .models import User
from .mail_branding import mail_branding

CODE_TTL = 300
SEND_COOLDOWN = 60
MAX_ATTEMPTS = 5


def normalize_email(value):
    if not isinstance(value, str):
        return None
    value = value.strip().lower()
    if len(value) > 64 or not re.fullmatch(r'[^\s@<>\x00-\x1f\x7f]+@[^\s@<>\x00-\x1f\x7f]+\.[^\s@<>\x00-\x1f\x7f]+', value):
        return None
    return value


def email_users(email):
    # Older OAuth profiles may have stored mixed-case addresses.
    return User.query.filter(func.lower(func.trim(User.email)) == email)


def code_digest(challenge_id, email, code):
    payload = 'email-login:{}:{}:{}'.format(challenge_id, email, code)
    return hmac.new(str(current_app.config['SECRET_KEY']).encode('utf-8'),
                    payload.encode('utf-8'), hashlib.sha256).hexdigest()


def send_login_code(email, code):
    """Use the existing SMTP account with bounded timeout and confirmed delivery.

    Login mail is synchronous so the API never reports success after SMTP failure.
    Transport errors are handled by the caller without logging message contents.
    """
    config = current_app.config
    site_name, sender = mail_branding()
    if not config.get('MAIL_SERVER') or not sender[1] or not config.get('MAIL_USERNAME') or not config.get('MAIL_PASSWORD'):
        raise RuntimeError('SMTP is not configured')
    message = EmailMessage()
    message['Subject'] = '{} · 登录验证码'.format(site_name)
    message['From'] = formataddr(sender) if isinstance(sender, (tuple, list)) else sender
    message['To'] = email
    sentence = '你正在使用 {} 账号登录网站「{}」。'.format(email, site_name)
    message.set_content('{}\n\n验证码：{}\n有效期：5 分钟。\n\n请勿将验证码告知他人。如非本人操作，请忽略此邮件。'.format(sentence, code))
    message.add_alternative(
        '<div style="max-width:480px;margin:32px auto;padding:24px;font-family:sans-serif;line-height:1.8;color:#333">'
        '<h2 style="font-size:20px">登录验证码</h2><p>{}</p>'
        '<p style="font-size:30px;font-weight:600;letter-spacing:6px">{}</p>'
        '<p>有效期：5 分钟。</p><p style="font-size:13px;color:#666">'
        '请勿将验证码告知他人。如非本人操作，请忽略此邮件。</p></div>'.format(escape(sentence), escape(code)),
        subtype='html')
    context = ssl.create_default_context()
    if config.get('MAIL_USE_SSL'):
        transport = smtplib.SMTP_SSL(config['MAIL_SERVER'], config.get('MAIL_PORT', 465), timeout=10, context=context)
    else:
        transport = smtplib.SMTP(config['MAIL_SERVER'], config.get('MAIL_PORT', 587), timeout=10)
    with transport as smtp:
        if not config.get('MAIL_USE_SSL'):
            smtp.starttls(context=context)
        smtp.login(config['MAIL_USERNAME'], config['MAIL_PASSWORD'])
        refused = smtp.send_message(message)
        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)
