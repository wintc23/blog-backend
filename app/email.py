from threading import Thread
from flask import current_app, render_template
from flask_mail import Message
from . import mail
from .defines import NOTIFY
from .mail_branding import mail_branding
import time
def send_async_email(app, data, to, subject):
  with app.app_context():
    site_name, sender = mail_branding()
    subject = "[{}] {}".format(site_name, subject)
    msg = Message(subject, sender=sender, recipients=[to])
    data.update(site_name=site_name, site_url=app.config.get("DOMAIN") or "")
    msg.body = ''
    msg.html = render_template('email.html', **data)
    mail.send(msg)

def send_email(to, subject, **kwargs):
  data = { **kwargs }
  data['NOTIFY'] = NOTIFY
  app = current_app._get_current_object()
  thr = Thread(target=send_async_email, args=[app, data, to, subject])
  thr.start()
  return thr