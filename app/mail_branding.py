"""Use the website's database identity for every outgoing email."""
from email.utils import parseaddr
from flask import current_app
from .models import PersonalProfile


def mail_branding():
    config = current_app.config
    profile = PersonalProfile.query.get(1)
    names = [profile.site_name if profile else '', config.get('SITE_NAME')]
    name = next((value.strip() for value in names if value and value.strip()), '本站')
    configured = config.get('MAIL_SENDER') or config.get('MAIL_USERNAME') or ''
    address = configured[1] if isinstance(configured, (tuple, list)) else parseaddr(configured)[1]
    return name, (name, address)
