from threading import Thread
from flask import current_app, render_template
from flask_mail import Message
from . import mail
from .defines import NOTIFY
import time
def send_async_email(app, data, to, subject):
  with app.app_context():
    subject = "{} {}".format(app.config['MAIL_SUBJECT_PREFIX'], subject)
    msg = Message(subject, sender=app.config['MAIL_SENDER'], recipients=[to])
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