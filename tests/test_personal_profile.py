"""Isolated API and migration tests; never connect to the site's database."""
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import unittest

os.environ.setdefault('FLASK_POSTS_PER_PAGE', '10')
os.environ.setdefault('FLASK_BBS_PER_PAGE', '10')

from flask import g, request
from sqlalchemy import create_engine, inspect
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app import create_app, db
from app.models import PersonalProfile


class PersonalProfileTests(unittest.TestCase):
  def setUp(self):
    self.app = create_app('testing')
    self.app.config.update(SQLALCHEMY_DATABASE_URI='sqlite://', TESTING=True)

    def test_identity():
      role = request.headers.get('X-Test-Role')
      if role:
        g.current_user = SimpleNamespace(can=lambda permission: role == 'admin')

    self.app.before_request_funcs.setdefault('api', []).append(test_identity)

    self.context = self.app.app_context()
    self.context.push()
    PersonalProfile.__table__.create(db.engine)
    self.client = self.app.test_client()
    self.data = {
      'display_name': '测试用户', 'avatar_url': 'https://example.com/avatar.jpg',
      'tagline': '测试简介', 'introduction': '介绍开头', 'bio': '第一段\n\n第二段',
      'contact_email': 'hello@example.com', 'wechat_id': 'example',
      'wechat_qr_url': 'https://example.com/qr.png', 'contact_note': '欢迎交流',
      'links': [{'label': '联系', 'url': 'mailto:hello@example.com'}, {'label': '关于', 'url': '/about'}],
      'moments': [],
    }

  def tearDown(self):
    db.session.remove()
    db.engine.dispose()
    self.context.pop()

  def put(self, data):
    return self.client.put('/api/personal-profile/', json=data, headers={'X-Test-Role': 'admin'})

  def test_empty_read_does_not_create_record(self):
    response = self.client.get('/api/personal-profile/')
    self.assertEqual(response.status_code, 200)
    self.assertEqual(response.get_json()['display_name'], '')
    self.assertEqual(PersonalProfile.query.count(), 0)

  def test_only_admin_can_save(self):
    self.assertEqual(self.client.put('/api/personal-profile/', json=self.data).status_code, 401)
    self.assertEqual(self.client.put('/api/personal-profile/', json=self.data, headers={'X-Test-Role': 'user'}).status_code, 403)
    self.assertEqual(PersonalProfile.query.count(), 0)

  def test_save_read_update_and_clear_optional_fields(self):
    self.assertEqual(self.put(self.data).status_code, 200)
    self.assertEqual(self.client.get('/api/personal-profile/').get_json(), dict(self.data, id=1))
    updated = dict(self.data, display_name='新的显示名称', bio='更新后的正文', links=[])
    self.assertEqual(self.put(updated).status_code, 200)
    db.session.remove()
    self.assertEqual(self.client.get('/api/personal-profile/').get_json(), dict(updated, id=1))
    self.assertEqual(PersonalProfile.query.count(), 1)
    self.assertEqual(self.put({'display_name': '只显示姓名'}).status_code, 200)
    self.assertEqual(self.client.get('/api/personal-profile/').get_json()['avatar_url'], '')

  def test_invalid_input_does_not_overwrite_saved_profile(self):
    self.put(self.data)
    invalid = [None, [], {'display_name': ' '}, dict(self.data, bio='x' * 10001),
      dict(self.data, introduction=123), dict(self.data, links=[{}]),
      dict(self.data, links=self.data['links'] * 5), dict(self.data, links='invalid')]
    for payload in invalid:
      self.assertEqual(self.put(payload).status_code, 400)
      self.assertEqual(self.client.get('/api/personal-profile/').get_json(), dict(self.data, id=1))

  def test_unsafe_links_are_rejected(self):
    self.put(self.data)
    for url in ('javascript:alert(1)', 'data:text/html,x', '//example.com', '/\\example.com', 'https://exa\nmple.com', 'mailto:invalid'):
      response = self.put(dict(self.data, links=[{'label': '链接', 'url': url}]))
      self.assertEqual(response.status_code, 400, url)
    self.assertEqual(self.put(dict(self.data, avatar_url='javascript:alert(1)')).status_code, 400)

  def test_link_groups_and_icons_survive_save_and_read(self):
    links = [
      {'label': 'GitHub', 'url': 'https://github.com/example', 'group': 'community', 'icon': 'github'},
      {'label': '文章归档', 'url': '/blog', 'group': 'navigation', 'icon': 'link'},
      {'label': '中山大学', 'url': 'https://www.sysu.edu.cn/', 'group': 'education', 'icon': 'sysu'},
      {'label': '字节跳动', 'url': 'https://www.bytedance.com/zh/', 'group': 'work', 'icon': 'bytedance'},
      self.data['links'][0],
    ]
    payload = dict(self.data, links=links)
    self.assertEqual(self.put(payload).status_code, 200)
    db.session.remove()
    self.assertEqual(self.client.get('/api/personal-profile/').get_json(), dict(payload, id=1))
    for changes in ({'group': 'invalid'}, {'icon': 'invalid'}, {'group': []}, {'icon': None}):
      invalid_links = [dict(links[0], **changes)]
      self.assertEqual(self.put(dict(payload, links=invalid_links)).status_code, 400)
      self.assertEqual(self.client.get('/api/personal-profile/').get_json(), dict(payload, id=1))

  def test_contact_validation_and_legacy_update(self):
    self.put(self.data)
    for changes in ({'contact_email': 'invalid'}, {'contact_email': 'a@example.com?bcc=b@example.com'},
                    {'wechat_qr_url': 'javascript:alert(1)'}, {'contact_note': 123}):
      self.assertEqual(self.put(dict(self.data, **changes)).status_code, 400)
      self.assertEqual(self.client.get('/api/personal-profile/').get_json(), dict(self.data, id=1))
    legacy = {key: value for key, value in self.data.items()
              if key not in ('contact_email', 'wechat_id', 'wechat_qr_url', 'contact_note')}
    self.assertEqual(self.put(legacy).status_code, 200)
    self.assertEqual(self.client.get('/api/personal-profile/').get_json(), dict(self.data, id=1))
    self.assertEqual(self.put(dict(self.data, contact_email='', wechat_id='', wechat_qr_url='', contact_note='')).status_code, 200)
    self.assertEqual(self.client.get('/api/personal-profile/').get_json()['contact_email'], '')

  def test_life_moments_persist_sort_update_and_clear(self):
    older = {'id': str(uuid4()), 'date': '2026-09-01', 'category': 'hiking',
             'text': '沿着山路走了一下午。', 'image_url': 'https://example.com/hike.jpg',
             'image_alt': '山间步道', 'location': '深圳'}
    newer = dict(older, id=str(uuid4()), date='2026-09-16', category='travel', text='路上的风景。')
    payload = dict(self.data, moments=[older, newer])
    self.assertEqual(self.put(payload).status_code, 200)
    db.session.remove()
    self.assertEqual(self.client.get('/api/personal-profile/').get_json()['moments'], [newer, older])
    legacy = {key: value for key, value in self.data.items() if key != 'moments'}
    self.assertEqual(self.put(legacy).status_code, 200)
    self.assertEqual(self.client.get('/api/personal-profile/').get_json()['moments'], [newer, older])
    self.assertEqual(self.put(dict(self.data, moments=[dict(newer, text='更新短文')])).status_code, 200)
    self.assertEqual(self.client.get('/api/personal-profile/').get_json()['moments'][0]['text'], '更新短文')
    self.assertEqual(self.put(self.data).status_code, 200)
    self.assertEqual(self.client.get('/api/personal-profile/').get_json()['moments'], [])

  def test_invalid_life_moments_do_not_change_profile(self):
    self.put(self.data)
    moment = {'id': str(uuid4()), 'date': '2026-09-16', 'category': 'mountain',
              'text': '山顶的风。', 'image_url': 'https://example.com/photo.jpg'}
    invalid = [None, {}, [None], [moment] * 13, [moment, moment]]
    invalid.append([dict(moment, id=str(uuid4()), image_url='https://example.com/' + '风' * 2000) for _ in range(12)])
    for changes in ({'date': '2026-02-30'}, {'date': '2026-2-01'}, {'text': ' '},
                    {'text': '字' * 281}, {'category': 'invalid'}, {'id': 'invalid'},
                    {'image_url': 'javascript:alert(1)'}, {'image_url': '//example.com/x.jpg'},
                    {'image_url': ''}, {'location': 123}):
      invalid.append([dict(moment, **changes)])
    for moments in invalid:
      self.assertEqual(self.put(dict(self.data, moments=moments)).status_code, 400, repr(moments)[:80])
      self.assertEqual(self.client.get('/api/personal-profile/').get_json(), dict(self.data, id=1))


class ProfileMigrationTests(unittest.TestCase):
  def test_upgrade_and_downgrade(self):
    migrations = []
    for name in ('20260915_personal_profile', '20260915_profile_contacts', '20260916_profile_moments'):
      path = Path(__file__).parents[1] / ('migrations/versions/' + name + '.py')
      spec = importlib.util.spec_from_file_location(name, path)
      migration = importlib.util.module_from_spec(spec)
      spec.loader.exec_module(migration)
      migrations.append(migration)
    engine = create_engine('sqlite://')
    with engine.connect() as connection:
      with Operations.context(MigrationContext.configure(connection)):
        for migration in migrations:
          migration.upgrade()
        self.assertIn('personal_profiles', inspect(connection).get_table_names())
        self.assertEqual(set(PersonalProfile.__table__.columns.keys()), {c['name'] for c in inspect(connection).get_columns('personal_profiles')})
        for migration in reversed(migrations):
          migration.downgrade()
        self.assertNotIn('personal_profiles', inspect(connection).get_table_names())
    engine.dispose()


if __name__ == '__main__':
  unittest.main()
