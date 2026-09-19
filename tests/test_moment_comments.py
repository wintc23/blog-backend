"""Isolated API regressions for life-moment discussions."""
from datetime import date
import unittest
import test_rich_comments as fixture
from app import db
from app.models import LifeMoment, Comment
from app.interaction_notifications import InteractionNotification


class MomentCommentTests(unittest.TestCase):
    owner = fixture.RichCommentTests.owner
    regular = fixture.RichCommentTests.regular
    post = fixture.RichCommentTests.post
    def setUp(self):
        fixture.RichCommentTests.setUp(self)
        LifeMoment.__table__.create(db.engine)
        rows = [LifeMoment(date=date.today(), category='daily', text='测试动态', image_url='') for _ in range(2)]
        db.session.add_all(rows)
        db.session.commit()
        self.ids = [row.id for row in rows]

    def tearDown(self):
        fixture.RichCommentTests.tearDown(self)

    def test_moderation_reply_notifications_and_delete(self):
        response = self.client.post('/api/add-comment/', headers=self.headers, json={'moment_id': self.ids[0], 'body': '动态评论'})
        self.assertEqual(response.status_code, 200, response.get_json())
        row = response.get_json()['comments'][0]
        self.assertTrue(row['hide'])
        self.assertEqual(row['target_url'], '/moments/' + self.ids[0])
        self.assertEqual(InteractionNotification.query.count(), 2)
        self.assertEqual(self.client.get('/api/comments/', query_string={'moment_id': self.ids[0]}).get_json()['comment_times'], 0)
        self.client.get('/api/set-comment-show/' + str(row['id']), headers=self.admin_headers)
        self.assertEqual(self.client.get('/api/comments/', query_string={'moment_id': self.ids[0]}).get_json()['comment_times'], 1)
        for payload in [
            {'moment_id': self.ids[1], 'response_id': row['id']},
            {'moment_id': self.ids[0], 'digest_id': 6},
            {'moment_id': '00000000-0000-0000-0000-000000000000'}
        ]:
            result = self.client.post('/api/add-comment/', headers=self.admin_headers, json=dict(body='不应提交', **payload))
            self.assertEqual(result.status_code, 404)
        reply = self.client.post('/api/add-comment/', headers=self.admin_headers, json={'moment_id': self.ids[0], 'response_id': row['id'], 'body': '回复'})
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(len(reply.get_json()['comments']), 2)
        db.session.delete(LifeMoment.query.get(self.ids[0]))
        db.session.commit()
        self.assertEqual(Comment.query.count(), 0)

    def test_invalid_target_and_edit(self):
        for target in [False, 1, {}, [], '']:
            self.assertEqual(self.client.post('/api/add-comment/', headers=self.headers, json={'moment_id': target, 'body': '评论'}).status_code, 400)
        result = self.client.post('/api/add-comment/', headers=self.admin_headers, json={'moment_id': self.ids[0], 'body': '第一版'})
        cid = result.get_json()['comments'][0]['id']
        self.assertEqual(self.client.put('/api/comments/%s/' % cid, headers=self.admin_headers, json={'body': '修改'}).status_code, 200)
        self.assertEqual(self.client.put('/api/comments/%s/' % cid, headers=self.headers, json={'body': '盗改'}).status_code, 404)

    def test_additive_migration_preserves_existing_comments(self):
        import importlib.util
        from pathlib import Path
        from sqlalchemy import create_engine, text, inspect
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        path = Path(__file__).resolve().parents[1] / 'migrations/versions/20260919_moment_comments.py'
        spec = importlib.util.spec_from_file_location('moment_migration', str(path))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        engine = create_engine('sqlite://')
        with engine.begin() as conn:
            conn.execute(text('CREATE TABLE life_moments (id VARCHAR(36) PRIMARY KEY)'))
            conn.execute(text('CREATE TABLE comments (id INTEGER PRIMARY KEY, body TEXT)'))
            conn.execute(text("INSERT INTO comments (id, body) VALUES (1, 'existing')"))
            module.op = Operations(MigrationContext.configure(conn))
            module.upgrade()
            self.assertEqual(tuple(conn.execute(text('SELECT body, moment_id FROM comments')).first()), ('existing', None))
            self.assertEqual(inspect(conn).get_foreign_keys('comments')[0]['referred_table'], 'life_moments')
            module.downgrade()
            self.assertEqual(conn.execute(text('SELECT body FROM comments')).scalar(), 'existing')
        engine.dispose()
