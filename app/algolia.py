# algolia搜索服务
from flask import current_app
from algoliasearch.search_client import SearchClient
from .models import Post

def get_index_context():
  app_id = current_app.config['ALGOLIA_APP_ID']
  admin_api_key = current_app.config['ALGOLIA_ADMIN_API_KEY']
  index_name = current_app.config['ALGOLIA_INDEX']
  client = SearchClient.create(app_id, admin_api_key)
  index = client.init_index(index_name)
  return index

def save_objects(data_list, data_type):
  print('save_objects', data_list)
  for i in range(len(data_list)):
    data_list[i]['objectID'] = '%s_%s' % (data_type, data_list[i]['id'])
  index = get_index_context()
  index.save_objects(data_list, { 'autoGenerateObjectIDIfNotExist': True })

def delete_objects(id_list, data_type):
  for i in range(len(id_list)):
    id_list[i] = '{}_{}'.format(data_type, id_list[i])
  index = get_index_context()
  index.delete_objects(id_list)
  
# 对已存在的数据进行保存
def save_all_posts():
  post_list = Post.query.filter_by(hide = False).all()
  post_list = filter(lambda p: not p.type.special, post_list)
  data_list = list(map(lambda p: p.to_json(), post_list))
  save_objects(data_list, 'post')
  print('save done')

# 对已存在数据删除
def delete_all_posts():
  post_list = Post.query.all()
  id_list = list(map(lambda x: x.id, post_list))
  delete_objects(id_list, 'post')
  print('clear done')