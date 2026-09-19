"""Transactional, independently retried email and lark-cli owner notifications."""
import hashlib
import os
import uuid
from datetime import datetime, timedelta
from urllib.parse import urlsplit
from flask import current_app, render_template
from flask_mail import Message
from . import db, mail
from .mail_branding import mail_branding


class InteractionNotification(db.Model):
    __tablename__ = 'interaction_notifications'
    id = db.Column(db.String(64), primary_key=True)
    channel = db.Column(db.String(8), nullable=False)
    title = db.Column(db.String(80), nullable=False)
    body = db.Column(db.Text, nullable=False)
    path = db.Column(db.String(512), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    available_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    sent_at = db.Column(db.DateTime)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    lease = db.Column(db.String(32))
    last_error = db.Column(db.String(80))


def enqueue(event, actor, title, content, path):
    """Caller commits along with the interaction. Re-liking never floods the owner."""
    if actor.is_administrator():
        return
    for channel in ('email', 'lark'):
        key = hashlib.sha256((event + ':' + str(actor.id) + ':' + channel).encode()).hexdigest()
        if not InteractionNotification.query.get(key):
            db.session.add(InteractionNotification(id=key, channel=channel, title=title,
                body='{}：{}'.format(actor.username, content[:600]), path=path))


def site_link(path):
    base = (current_app.config.get('NOTIFICATION_SITE_URL') or os.environ.get('NOTIFICATION_SITE_URL')
            or current_app.config.get('DOMAIN') or '').rstrip('/')
    parsed = urlsplit(base)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or '\\' in base or any(c.isspace() for c in base)):
        raise ValueError('Configure a valid NOTIFICATION_SITE_URL or DOMAIN')
    if not path.startswith('/') or path.startswith('//'):
        raise ValueError('Invalid notification path')
    return base + path


def deliver(row):
    url = site_link(row.path)
    if row.channel == 'email':
        recipient = current_app.config.get('FLASK_ADMIN')
        if not recipient:
            raise ValueError('FLASK_ADMIN is required')
        site_name, sender = mail_branding()
        message = Message('[{}] {}'.format(site_name, row.title), sender=sender, recipients=[recipient])
        message.body = '{}\n\n{}'.format(row.body, url)
        message.html = render_template('interaction-email.html', title=row.title, body=row.body, url=url, site_name=site_name)
        mail.send(message)
    else:
        from lark_bridge.lark import Lark
        client = Lark(os.environ.get('LARK_BRIDGE_CLI', 'lark-cli'), os.getcwd(), os.environ['LARK_BRIDGE_APP_ID'])
        card = {'config': {'wide_screen_mode': True},
                'header': {'title': {'tag': 'plain_text', 'content': row.title}, 'template': 'blue'},
                'elements': [{'tag': 'div', 'text': {'tag': 'plain_text', 'content': row.body}},
                             {'tag': 'action', 'actions': [{'tag': 'button', 'text': {'tag': 'plain_text', 'content': '查看详情'},
                                                          'type': 'primary', 'url': url}]}]}
        client.send_card(os.environ['LARK_BRIDGE_OWNER_OPEN_ID'], card, row.id)


def run_one():
    now = datetime.utcnow()
    row = InteractionNotification.query.filter(InteractionNotification.sent_at.is_(None),
        InteractionNotification.available_at <= now).order_by(InteractionNotification.available_at, InteractionNotification.id).first()
    if not row:
        return False
    key, lease = row.id, uuid.uuid4().hex
    claimed = InteractionNotification.query.filter_by(id=key, sent_at=None).filter(
        InteractionNotification.available_at <= now).update({
            'lease': lease, 'available_at': now + timedelta(minutes=5),
            'attempts': InteractionNotification.attempts + 1}, synchronize_session=False)
    db.session.commit()
    if not claimed:
        return True
    row = InteractionNotification.query.get(key)
    try:
        deliver(row)
    except Exception as error:
        # Do not log credentials, share tokens, user text or SMTP responses.
        InteractionNotification.query.filter_by(id=key, lease=lease).update({
            'lease': None, 'last_error': type(error).__name__,
            'available_at': datetime.utcnow() + timedelta(seconds=min(21600, 30 * 2 ** min(row.attempts, 10)))})
        current_app.logger.warning('Interaction notification retry: %s %s', row.channel, type(error).__name__)
    else:
        InteractionNotification.query.filter_by(id=key, lease=lease).update({
            'sent_at': datetime.utcnow(), 'lease': None, 'last_error': None, 'body': ''})
    db.session.commit()
    return True
