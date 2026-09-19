"""Reactions are idempotent, guest-enabled and scoped to visible content."""
import hashlib
import importlib.util
from datetime import date, datetime, timedelta
from pathlib import Path
import unittest
from alembic.migration import MigrationContext
from alembic.operations import Operations
import test_image_tools as fixture
from app import db
from app.models import LifeMoment, StatEvent
from app.content_like_models import ContentLike
from app.image_tool_models import ImageTool, ImageTask

class ContentLikesTests(unittest.TestCase):
    owner = fixture.ImageToolsTests.owner
    regular = fixture.ImageToolsTests.regular
    create = fixture.ImageToolsTests.create
    def setUp(self):
        fixture.ImageToolsTests.setUp(self)
        for model in (LifeMoment, ContentLike): model.__table__.create(db.engine)
        db.session.add(LifeMoment(id='moment-one', date=date.today(), category='daily', text='hello', image_url=''))
        db.session.commit()
    def tearDown(self): fixture.ImageToolsTests.tearDown(self)
    def test_guest_identity_idempotence_and_counts(self):
        path = '/api/life-moments/moment-one/likes/'
        self.assertEqual(self.client.get(path).get_json(), {'likes': 0, 'like': False})
        self.assertEqual(self.client.post(path).status_code, 401)
        guest = self.client.post('/api/guest-login/', json={}).get_json()
        headers = {'Authorization': guest['token']}
        for _ in range(2): self.assertEqual(self.client.post(path, headers=headers).get_json(), {'likes': 1, 'like': True})
        self.assertEqual(StatEvent.query.filter_by(name='content.like').count(), 1)
        self.assertEqual(self.client.get(path).get_json(), {'likes': 1, 'like': False})
        self.assertEqual(self.client.post(path, headers=self.headers).get_json()['likes'], 2)
        self.assertEqual(self.client.get(path, headers=headers).get_json(), {'likes': 2, 'like': True})
        for _ in range(2): self.assertEqual(self.client.delete(path, headers=headers).get_json(), {'likes': 1, 'like': False})
        self.assertEqual(self.client.get(path, headers={'Authorization': 'invalid'}).status_code, 401)
    def test_visibility_and_share_tokens(self):
        path = '/api/image-tools/cartoon/likes/'
        self.assertEqual(self.client.post(path, headers=self.headers).status_code, 200)
        tool = ImageTool.query.filter_by(slug='cartoon').one(); tool.enabled = False; db.session.commit()
        self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.post('/api/life-moments/missing/likes/', headers=self.headers).status_code, 404)
        ImageTool.query.filter_by(slug='cartoon').one().enabled = True; db.session.commit()
        task_id = self.create(); task = ImageTask.query.get(task_id); task.owner_id = self.owner_id; token = 'private-random-share-token'
        task.share_hash = hashlib.sha256(token.encode()).hexdigest(); task.share_until = datetime.utcnow() + timedelta(days=1); db.session.commit()
        path = '/api/image-shares/' + token + '/likes/'
        self.assertEqual(self.client.post(path, headers=self.headers).get_json(), {'likes': 1, 'like': True})
        self.assertEqual(ContentLike.query.filter_by(kind='image_share').one().target_id, hashlib.sha256(token.encode()).hexdigest())
        self.assertNotIn(token, ''.join(event.params or '' for event in StatEvent.query.all()))
        task = ImageTask.query.get(task_id); task.share_until = datetime.utcnow() - timedelta(seconds=1); db.session.commit()
        self.assertEqual(self.client.post(path, headers=self.headers).status_code, 404)
        task = ImageTask.query.get(task_id); task.share_until = datetime.utcnow() + timedelta(days=1); task.deleted_at = datetime.utcnow(); db.session.commit()
        self.assertEqual(self.client.get(path).status_code, 404)
        task = ImageTask.query.get(task_id); task.deleted_at = None; task.share_hash = None; db.session.commit()
        self.assertEqual(self.client.get(path).status_code, 404)
    def test_guest_like_rate_limit(self):
        guest = self.client.post('/api/guest-login/', json={}).get_json(); headers = {'Authorization': guest['token']}
        for n in range(6):
            db.session.add(LifeMoment(id='m'+str(n), date=date.today(), category='daily', text='x', image_url=''))
        db.session.commit()
        for n in range(6):
            result = self.client.post('/api/life-moments/m{}/likes/'.format(n), headers=headers)
            self.assertEqual(result.status_code, 200 if n < 5 else 429)
        self.assertEqual(ContentLike.query.count(), 5)
    def test_migration_round_trip(self):
        ContentLike.__table__.drop(db.engine)
        spec = importlib.util.spec_from_file_location('content_likes_migration', Path(__file__).parents[1] / 'migrations/versions/20260919_content_likes.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        with db.engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                module.upgrade(); module.downgrade(); module.upgrade()
        self.assertEqual(ContentLike.query.count(), 0)

if __name__ == '__main__': unittest.main()
