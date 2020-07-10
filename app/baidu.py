# 百度推送

import os

from flask import current_app

def auto_push(push_type, post_id):
  if not push_type:
    msg =  'auto push fail with params push_type is blank'
    return { 'error_msg': msg }
  token = current_app.config['BAIDU_TOKEN']
  if current_app.config['ENV'] != 'production' or not token:
    msg = 'auto push fail, env:{}, token: {} type: {}'.format(current_app.config['ENV'], bool(token), push_type)
    return { 'error_msg': msg }
  target = 'http://data.zz.baidu.com/{}?site=wintc.top&token={}'.format(push_type, token)
  data = 'https://wintc.top/article/{}'.format(post_id)
  res = os.popen('''
  curl -H 'Content-Type:text/plain' --data-raw {} "{}"
  '''.format(data, target)).readlines()
  return { 'res': res, 'push_type': push_type, 'url': data, 'error_msg': '' }
