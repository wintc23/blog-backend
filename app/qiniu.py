from qiniu import Auth
from flask import current_app

def get_token(filename, max_size=None, mime_limit=None):
  policy = {
    "returnBody": "{ \"name\": $(fname), \"key\": $(key) }"
  }
  access_key = current_app.config['QI_NIU_ACCESS_KEY']
  if max_size is not None:
    policy['fsizeLimit'] = max_size
  if mime_limit is not None:
    policy['mimeLimit'] = mime_limit
  secret_key = current_app.config['QI_NIU_SECRET_KEY']
  bucket = current_app.config['QI_NIU_BUCKET']

  q = Auth(access_key, secret_key)
  token = q.upload_token(bucket, filename, 600, policy)
  return token
