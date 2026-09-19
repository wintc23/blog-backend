"""Isolated interaction outbox and mocked transports: no real messages sent."""
import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
import test_content_likes as content_fixture
from app import db
from app.interaction_notifications import InteractionNotification as Notice, enqueue, run_one, deliver, site_link
from app.models import Like
from app.digest_models import AiDigest, AiDigestLike


class NotificationTests(unittest.TestCase):
    owner = content_fixture.ContentLikesTests.owner
    regular = content_fixture.ContentLikesTests.regular
    create = content_fixture.ContentLikesTests.create

    def setUp(self):
        content_fixture.ContentLikesTests.setUp(self)
        self.app.config.update(FLASK_ADMIN='owner@example.test', MAIL_SENDER='site@example.test')

    def tearDown(self):
        content_fixture.ContentLikesTests.tearDown(self)

    def test_likes_are_transactional_deduplicated_and_skip_administrator(self):
        path = '/api/life-moments/moment-one/likes/'
        with patch('app.interaction_notifications.deliver') as transport:
            for _ in range(2):
                self.assertEqual(self.client.post(path, headers=self.headers).status_code, 200)
            self.assertEqual(Notice.query.count(), 2)
            self.client.delete(path, headers=self.headers)
            self.client.post(path, headers=self.headers)
            self.client.post(path, headers=self.admin_headers)
            self.assertEqual(Notice.query.count(), 2)
            transport.assert_not_called()
        enqueue('rollback', self.regular, '测试', '不会发送', '/moments')
        db.session.rollback()
        self.assertEqual(Notice.query.count(), 2)

    def test_tool_post_digest_and_share_routes(self):
        AiDigest.__table__.create(db.engine)
        AiDigestLike.__table__.create(db.engine)
        from test_likes import LikeTests
        digest_id = LikeTests.issue(self)
        for path in ['/api/image-tools/cartoon/likes/', '/api/posts/{}/likes/'.format(self.post_id),
                     '/api/ai-digests/{}/likes/'.format(digest_id)]:
            self.assertEqual(self.client.post(path, headers=self.headers).status_code, 200)
        self.assertEqual(Notice.query.count(), 6)
        import hashlib
        from app.image_tool_models import ImageTask
        task_id = self.create()
        task = ImageTask.query.get(task_id)
        task.owner_id = self.owner_id
        token = 'do-not-persist-share-token'
        task.share_hash = hashlib.sha256(token.encode()).hexdigest()
        task.share_until = datetime.utcnow() + timedelta(days=1)
        db.session.commit()
        self.assertEqual(self.client.post('/api/image-shares/'+token+'/likes/', headers=self.headers).status_code, 200)
        self.assertEqual(Notice.query.count(), 8)
        for row in Notice.query.all():
            self.assertNotIn(token, row.path + row.body)

    def test_comments_messages_and_replies_enqueue_both_channels(self):
        comment = self.client.post('/api/add-comment/', headers=self.headers, json={'post_id': self.post_id, 'body': '评论通知'})
        self.assertEqual(comment.status_code, 200, comment.get_json())
        message = self.client.post('/api/add-message/', headers=self.headers, json={'body': '留言通知'})
        self.assertEqual(message.status_code, 200, message.get_json())
        response = self.client.post('/api/add-message/', headers=self.headers, json={'body': '留言回复通知', 'response_id': message.get_json()['id']})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(Notice.query.count(), 6)
        self.assertEqual({n.title for n in Notice.query.all()}, {'收到评论', '收到留言', '收到留言回复'})

    def test_channel_failure_retries_without_resending_success(self):
        enqueue('one', self.regular, '收到点赞', '赞了动态', '/moments/one')
        db.session.commit()
        sent = []
        def send(row):
            if row.channel == 'lark': raise RuntimeError('secret must not persist')
            sent.append(row.id)
        with patch('app.interaction_notifications.deliver', side_effect=send):
            self.assertTrue(run_one()); self.assertTrue(run_one()); self.assertFalse(run_one())
        email = Notice.query.filter_by(channel='email').one()
        lark = Notice.query.filter_by(channel='lark').one()
        self.assertIsNotNone(email.sent_at)
        self.assertEqual(email.body, '')
        self.assertEqual(lark.last_error, 'RuntimeError')
        self.assertGreater(lark.available_at, datetime.utcnow())
        lark.available_at = datetime.utcnow() - timedelta(seconds=1)
        db.session.commit()
        with patch('app.interaction_notifications.deliver') as send:
            self.assertTrue(run_one()); self.assertFalse(run_one())
            self.assertEqual(send.call_count, 1)
        self.assertEqual(Notice.query.filter(Notice.sent_at.isnot(None)).count(), 2)

    def test_expired_lease_recovers_and_workers_do_not_share_claim(self):
        enqueue('one', self.regular, '测试', '内容', '/moments')
        db.session.commit()
        row = Notice.query.first()
        row.lease = 'abandoned'; row.available_at = datetime.utcnow() + timedelta(minutes=1)
        db.session.commit()
        with patch('app.interaction_notifications.deliver') as send:
            self.assertTrue(run_one()); self.assertFalse(run_one())
            row.available_at = datetime.utcnow() - timedelta(seconds=1); db.session.commit()
            self.assertTrue(run_one())
            self.assertEqual(send.call_count, 2)
        enqueue('two', self.regular, '测试', '内容', '/moments'); db.session.commit()
        def work(_):
            with self.app.app_context():
                try: return run_one()
                finally: db.session.remove()
        with patch('app.interaction_notifications.deliver') as send:
            with ThreadPoolExecutor(max_workers=2) as pool: list(pool.map(work, range(2)))
            while run_one(): pass
            self.assertEqual(send.call_count, 2)

    def test_transports_escape_user_content_and_use_explicit_configuration(self):
        enqueue('one', self.regular, '收到点赞', '<script>test</script>', '/moments/one')
        db.session.commit()
        with patch('app.interaction_notifications.mail.send') as send:
            deliver(Notice.query.filter_by(channel='email').one())
            message = send.call_args[0][0]
            self.assertEqual(message.recipients, ['owner@example.test'])
            self.assertIn('&lt;script&gt;', message.html)
            self.assertIn('https://example.test/moments/one', message.body)
        with patch.dict(os.environ, {'LARK_BRIDGE_APP_ID': 'app', 'LARK_BRIDGE_OWNER_OPEN_ID': 'owner'}), patch('lark_bridge.lark.Lark.send_card') as send:
            row = Notice.query.filter_by(channel='lark').one()
            deliver(row)
            owner, card, key = send.call_args[0]
            self.assertEqual(owner, 'owner')
            self.assertEqual(card['elements'][0]['text']['tag'], 'plain_text')
            self.assertEqual(key, row.id)
        self.app.config['NOTIFICATION_SITE_URL'] = 'https://another.test/site/'
        self.assertEqual(site_link('/moments'), 'https://another.test/site/moments')
        self.app.config['NOTIFICATION_SITE_URL'] = 'javascript:bad'
        with self.assertRaises(ValueError): site_link('/moments')

    def test_migration(self):
        import importlib.util
        from pathlib import Path
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        spec = importlib.util.spec_from_file_location('notification_migration', Path(__file__).parents[1] / 'migrations/versions/20260919_interaction_notifications.py')
        migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
        Notice.__table__.drop(db.engine)
        with db.engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)): migration.upgrade()
        enqueue('migration', self.regular, '测试', '内容', '/moments'); db.session.commit()
        self.assertEqual(Notice.query.count(), 2)
