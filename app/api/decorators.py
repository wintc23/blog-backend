from functools import wraps
from flask import g
from .errors import forbidden, unauthorized

def login_required(func):
  @wraps(func)
  def decorator(*args, **kwargs):
    if not g.current_user:
      return unauthorized('未登录，请先认证后再操作')
    return func(*args, **kwargs)
  return decorator