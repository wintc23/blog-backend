"""Independent post CRUD and legacy-data migration, using isolated SQLite only."""
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import unittest
from uuid import uuid4

os.environ.setdefault('FLASK_POSTS_PER_PAGE', '10')
os.environ.setdefault('FLASK_BBS_PER_PAGE', '10')

from flask import g, request
from sqlalchemy import create_engine, inspect, text
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app import create_app, db
from app.models import LifeMoment, PersonalProfile, Role, User, Comment


class LifeMomentTests(unittest.TestCase):
  def setUp(self):
    self.app = create_app('testing')
    self.app.config.update(SQLALCHEMY_DATABASE_URI='sqlite://', TESTING=True, QI_NIU_LINK_URL='https://media.example.test')
    def identity():
      role = request.headers.get('X-Test-Role')
      if role:
        g.current_user = SimpleNamespace(can=lambda permission: role == 'admin')
    self.app.before_request_funcs.setdefault('api', []).append(identity)
    self.context = self.app.app_context()
    self.context.push()
    from app.media_models import MediaAsset, MediaReference
    MediaAsset.__table__.create(db.engine)
    MediaReference.__table__.create(db.engine)
    LifeMoment.__table__.create(db.engine)
    PersonalProfile.__table__.create(db.engine)
    Role.__table__.create(db.engine)
    User.__table__.create(db.engine)
    Comment.__table__.create(db.engine)
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
    for changes in ({'text': ''}, {'text': '字' * 2001}, {'date': '2026-02-30'}, {'image_url': 'javascript:alert(1)'}, {'category': 'unknown'}):
      response = self.client.put('/api/life-moments/{}/'.format(record['id']), json=dict(self.data, **changes), headers=self.headers)
      self.assertEqual(response.status_code, 400)
    for query in ('page=-1', 'page=nope', 'per_page=0', 'per_page=31'):
      self.assertEqual(self.client.get('/api/life-moments/?' + query).status_code, 400)
    self.assertEqual(LifeMoment.query.get(record['id']).text, self.data['text'])

  def test_time_images_captions_and_detail_round_trip(self):
    images = [{'url': 'https://example.com/mountain.jpg', 'description': '清晨的山路'},
              {'url': 'https://example.com/lake.jpg', 'description': '湖面 🌊'}]
    record = self.publish(occurred_at='2026-09-16T18:30:00Z', images=images, text='文字在上\n' + '景色' * 200)
    self.assertEqual(record['date'], '2026-09-17')
    self.assertEqual(record['occurred_at'], '2026-09-17T02:30:00+08:00')
    self.assertEqual(record['images'], images)
    self.assertEqual(record['image_url'], images[0]['url'])
    path = '/api/life-moments/{}/'.format(record['id'])
    self.assertEqual(self.client.get(path).get_json(), record)
    response = self.client.put(path, headers=self.headers, json=dict(self.data, occurred_at=record['occurred_at'], images=list(reversed(images))))
    self.assertEqual(response.get_json()['images'], list(reversed(images)))
    self.assertEqual(self.client.delete(path, headers=self.headers).status_code, 200)
    self.assertEqual(self.client.get(path).status_code, 404)

  def test_grouped_pagination_never_splits_a_date_and_uses_occurrence_order(self):
    self.assertEqual(self.client.get('/api/life-moments/?group_by=date').get_json()['groups'], [])
    records = [self.publish(date='2026-09-{:02d}'.format(day)) for day in range(1, 10)]
    late = self.publish(occurred_at='2026-09-09T20:00:00+08:00')
    early = self.publish(occurred_at='2026-09-09T08:00:00+08:00')
    prefix = '/api/life-moments/?group_by=date&per_page=2&page='
    first = self.client.get(prefix + '1').get_json()
    self.assertEqual((first['total'], first['total_dates']), (11, 9))
    self.assertEqual([item['id'] for item in first['groups'][0]['moments']], [late['id'], early['id'], records[-1]['id']])
    seen_dates, seen_ids = [], []
    for page in range(1, 6):
      for group in self.client.get(prefix + str(page)).get_json()['groups']:
        seen_dates.append(group['date'])
        seen_ids.extend(moment['id'] for moment in group['moments'])
    self.assertEqual(len(seen_dates), len(set(seen_dates)))
    self.assertEqual(len(seen_ids), 11)
    self.assertEqual(len(set(seen_ids)), 11)
    self.assertEqual(self.client.get(prefix + '999').get_json()['page'], 5)
    recent = self.client.get('/api/life-moments/?per_page=3').get_json()['list']
    self.assertEqual([row['id'] for row in recent], [late['id'], early['id'], records[-1]['id']])

  def test_plain_text_and_legacy_dates_are_preserved_without_inventing_time(self):
    record = self.publish(images=[], text='2 < 3，<script>作为普通文字</script>')
    self.assertEqual(record['images'], [])
    self.assertEqual(record['image_url'], '')
    self.assertIsNone(record['occurred_at'])
    self.assertEqual(record['date'], self.data['date'])
    legacy = self.publish()
    self.assertEqual(legacy['images'], [{'url': self.data['image_url'], 'description': self.data['image_alt']}])

  def test_invalid_gallery_time_and_descriptions_are_rejected(self):
    for changes in ({'images': None}, {'images': [{}]}, {'images': [{'url': 'javascript:alert(1)'}]},
                    {'images': [{'url': 'https://example.com/x" onload="bad'}]},
                    {'images': [{'url': 'https://example.com/a.png', 'description': '字' * 201}]},
                    {'images': [{'url': 'https://example.com/a.png'}] * 10},
                    {'occurred_at': '2026-09-17T12:00'}, {'occurred_at': '2026-02-30T12:00:00+08:00'}):
      self.assertEqual(self.client.post('/api/life-moments/', json=dict(self.data, **changes), headers=self.headers).status_code, 400, changes)
    self.assertEqual(LifeMoment.query.count(), 0)
    self.assertEqual(self.client.get('/api/life-moments/?group_by=nope').status_code, 400)

  def test_gallery_references_survive_reordering_and_shared_images_cleanup_last(self):
    from app.media_models import MediaAsset, MediaReference
    images = []
    for seed in ('a', 'b'):
      key = 'managed-images/' + seed * 32 + '.jpg'
      url = 'https://media.example.test/' + key
      db.session.add(MediaAsset(id=seed * 32, storage_key=key, url=url, mime_type='image/jpeg', byte_size=123, width=10, height=10, status='ready'))
      images.append({'url': url, 'description': seed})
    db.session.commit()
    first = self.publish(images=images)
    second = self.publish(images=images[1:])
    path = '/api/life-moments/{}/'.format(first['id'])
    with patch('app.media.delete_image') as delete:
      response = self.client.put(path, json=dict(self.data, images=list(reversed(images))), headers=self.headers)
      self.assertEqual(response.status_code, 200)
      self.assertEqual(MediaReference.query.count(), 3)
      delete.assert_not_called()
      self.client.put(path, json=dict(self.data, images=[]), headers=self.headers)
      self.assertEqual(MediaReference.query.count(), 1)
      delete.assert_called_once_with('managed-images/' + 'a' * 32 + '.jpg')
      self.client.delete('/api/life-moments/{}/'.format(second['id']), headers=self.headers)
      self.assertEqual(MediaReference.query.count(), 0)
      self.assertEqual(delete.call_count, 2)

  def test_saving_profile_never_replaces_independent_posts(self):
    record = self.publish()
    response = self.client.put('/api/personal-profile/', json={'display_name': '测试用户', 'moments': []}, headers=self.headers)
    self.assertEqual(response.status_code, 200)
    self.assertEqual(LifeMoment.query.get(record['id']).text, self.data['text'])


class LifeMomentMigrationTests(unittest.TestCase):
  def test_existing_profile_moments_become_independent_records(self):
    migrations = []
    for name in ('20260915_personal_profile', '20260915_profile_contacts', '20260916_profile_moments', '20260916_life_moments', '20260917_moment_gallery'):
      spec = importlib.util.spec_from_file_location(name, Path(__file__).parents[1] / ('migrations/versions/' + name + '.py'))
      migration = importlib.util.module_from_spec(spec)
      spec.loader.exec_module(migration)
      migrations.append(migration)
    legacy = [{'id': str(uuid4()), 'date': '2026-09-16', 'category': 'hiking', 'text': '原有记录', 'image_url': 'https://example.com/a.jpg', 'image_alt': '', 'location': ''}]
    engine = create_engine('sqlite://')
    with engine.connect() as connection:
      with Operations.context(MigrationContext.configure(connection)):
        for migration in migrations[:3]:
          migration.upgrade()
        connection.execute(text("INSERT INTO personal_profiles (id, display_name, avatar_url, tagline, introduction, bio, links_json, moments_json, updated_at) VALUES (1, '用户', '', '', '', '保留的个人介绍', '[]', :moments, CURRENT_TIMESTAMP)"), moments=json.dumps(legacy))
        for migration in migrations[3:]:
          migration.upgrade()
        self.assertEqual(connection.execute(text('SELECT text FROM life_moments')).scalar(), '原有记录')
        self.assertEqual(connection.execute(text('SELECT bio FROM personal_profiles')).scalar(), '保留的个人介绍')
        row = connection.execute(text('SELECT images_json, occurred_at FROM life_moments')).first()
        self.assertEqual(json.loads(row[0]), [{'url': legacy[0]['image_url'], 'description': ''}])
        self.assertIsNone(row[1])
        self.assertEqual(set(LifeMoment.__table__.columns.keys()), {col['name'] for col in inspect(connection).get_columns('life_moments')})
        for migration in reversed(migrations):
          migration.downgrade()
    engine.dispose()


if __name__ == '__main__':
  unittest.main()
