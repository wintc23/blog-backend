from qiniu import Auth
from flask import current_app

def get_token(filename):
  policy = {
    "returnBody": "{ \"name\": $(fname), \"key\": $(key) }"
  }
  access_key = current_app.config['QI_NIU_ACCESS_KEY']
  secret_key = current_app.config['QI_NIU_SECRET_KEY']
  bucket = current_app.config['QI_NIU_BUCKET']

  q = Auth(access_key, secret_key)
  token = q.upload_token(bucket, filename, 600, policy)
  return token