"""Independent post CRUD and legacy-data migration, using isolated SQLite only."""
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from uuid import uuid4

os.environ.setdefault('FLASK_POSTS_PER_PAGE', '10')
os.environ.setdefault('FLASK_BBS_PER_PAGE', '10')

from flask import g, request
from sqlalchemy import create_engine, inspect, text
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app import create_app, db
from app.models import LifeMoment, PersonalProfile


class LifeMomentTests(unittest.TestCase):
  def setUp(self):
    self.app = create_app('testing')
    self.app.config.update(SQLALCHEMY_DATABASE_URI='sqlite://', TESTING=True)
    def identity():
      role = request.headers.get('X-Test-Role')
      if role:
        g.current_user = SimpleNamespace(can=lambda permission: role == 'admin')
    self.app.before_request_funcs.setdefault('api', []).append(identity)
    self.context = self.app.app_context()
    self.context.push()
    LifeMoment.__table__.create(db.engine)
    PersonalProfile.__table__.create(db.engine)
    self.client = self.app.test_client()
    self.headers = {'X-Test-Role': 'admin'}
    self.data = {'date': '2026-09-16', 'category': 'hiking', 'text': '山路上的风景 🌲',
                 'image_url': 'https://example.com/hike.jpg', 'image_alt': '山间步道', 'location': '深圳'}

  def tearDown(self):
    db.session.remove()
    db.engine.dispose()
    self.context.pop()

  def publish(self, **changes):
    response = self.client.post('/api/life-moments/', json=dict(self.data, **changes), headers=self.headers)
    self.assertEqual(response.status_code, 201)
    return response.get_json()

  def test_append_more_than_twelve_and_paginate_without_losing_history(self):
    self.assertEqual(self.client.get('/api/life-moments/').get_json()['list'], [])
    records = [self.publish(date='2026-09-{:02d}'.format(day)) for day in range(1, 17)]
    db.session.remove()
    recent = self.client.get('/api/life-moments/?per_page=3').get_json()
    self.assertEqual(recent['total'], 16)
    self.assertEqual([item['id'] for item in recent['list']], [item['id'] for item in reversed(records[-3:])])
    seen = []
    for page in range(1, 5):
      data = self.client.get('/api/life-moments/?per_page=5&page={}'.format(page)).get_json()
      seen.extend(item['id'] for item in data['list'])
    self.assertEqual(len(set(seen)), 16)
    self.assertEqual(seen[-1], records[0]['id'])
    self.assertEqual(self.client.get('/api/life-moments/?per_page=5&page=99').get_json()['page'], 4)

  def test_only_admin_can_write(self):
    record = self.publish()
    for headers, status in (({}, 401), ({'X-Test-Role': 'user'}, 403)):
      for method, path in (('post', '/api/life-moments/'), ('put', '/api/life-moments/{}/'.format(record['id'])), ('delete', '/api/life-moments/{}/'.format(record['id']))):
        response = getattr(self.client, method)(path, json=self.data, headers=headers)
        self.assertEqual(response.status_code, status)
    self.assertEqual(LifeMoment.query.count(), 1)

  def test_updates_and_deletes_only_the_selected_record(self):
    first, second = self.publish(), self.publish(text='另一段旅途')
    path = '/api/life-moments/{}/'.format(first['id'])
    response = self.client.put(path, json=dict(self.data, text='更新后的记录'), headers=self.headers)
    self.assertEqual(response.status_code, 200)
    self.assertEqual(LifeMoment.query.get(second['id']).text, second['text'])
    self.assertEqual(self.client.delete(path, headers=self.headers).status_code, 200)
    self.assertEqual(self.client.delete(path, headers=self.headers).status_code, 404)
    self.assertEqual(LifeMoment.query.count(), 1)
    self.assertEqual(LifeMoment.query.first().id, second['id'])

  def test_invalid_posts_and_pagination_are_rejected(self):
    record = self.publish()
    for changes in ({'text': ''}, {'text': '字' * 281}, {'date': '2026-02-30'}, {'image_url': 'javascript:alert(1)'}, {'category': 'unknown'}):
      response = self.client.put('/api/life-moments/{}/'.format(record['id']), json=dict(self.data, **changes), headers=self.headers)
      self.assertEqual(response.status_code, 400)
    for query in ('page=-1', 'page=nope', 'per_page=0', 'per_page=31'):
      self.assertEqual(self.client.get('/api/life-moments/?' + query).status_code, 400)
    self.assertEqual(LifeMoment.query.get(record['id']).text, self.data['text'])

  def test_saving_profile_never_replaces_independent_posts(self):
    record = self.publish()
    response = self.client.put('/api/personal-profile/', json={'display_name': '测试用户', 'moments': []}, headers=self.headers)
    self.assertEqual(response.status_code, 200)
    self.assertEqual(LifeMoment.query.get(record['id']).text, self.data['text'])


class LifeMomentMigrationTests(unittest.TestCase):
  def test_existing_profile_moments_become_independent_records(self):
    migrations = []
    for name in ('20260915_personal_profile', '20260915_profile_contacts', '20260916_profile_moments', '20260916_life_moments'):
      spec = importlib.util.spec_from_file_location(name, Path(__file__).parents[1] / ('migrations/versions/' + name + '.py'))
      migration = importlib.util.module_from_spec(spec)
      spec.loader.exec_module(migration)
      migrations.append(migration)
    legacy = [{'id': str(uuid4()), 'date': '2026-09-16', 'category': 'hiking', 'text': '原有记录', 'image_url': 'https://example.com/a.jpg', 'image_alt': '', 'location': ''}]
    engine = create_engine('sqlite://')
    with engine.connect() as connection:
      with Operations.context(MigrationContext.configure(connection)):
        for migration in migrations[:-1]:
          migration.upgrade()
        connection.execute(text("INSERT INTO personal_profiles (id, display_name, avatar_url, tagline, introduction, bio, links_json, moments_json, updated_at) VALUES (1, '用户', '', '', '', '保留的个人介绍', '[]', :moments, CURRENT_TIMESTAMP)"), moments=json.dumps(legacy))
        migrations[-1].upgrade()
        self.assertEqual(connection.execute(text('SELECT text FROM life_moments')).scalar(), '原有记录')
        self.assertEqual(connection.execute(text('SELECT bio FROM personal_profiles')).scalar(), '保留的个人介绍')
        self.assertEqual(set(LifeMoment.__table__.columns.keys()), {col['name'] for col in inspect(connection).get_columns('life_moments')})
        for migration in reversed(migrations):
          migration.downgrade()
    engine.dispose()


if __name__ == '__main__':
  unittest.main()
