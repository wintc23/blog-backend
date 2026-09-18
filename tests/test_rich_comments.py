"""Real API, database and image decoding; storage and notifications are isolated."""
import io
import importlib.util
from datetime import datetime, timedelta
from pathlib import Path
import unittest
from unittest.mock import patch
from PIL import Image
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
import test_guest_login as guest_fixture
from app import db
from app.models import Comment, Message, User
from app.digest_models import AiDigest
from app.media_models import MediaAsset, MediaReference
from app.media import cleanup_images, prepare_image
from app.rich_content import validate_body


class RichCommentTests(unittest.TestCase):
    owner = guest_fixture.GuestLoginTests.owner
    regular = guest_fixture.GuestLoginTests.regular
    post = guest_fixture.GuestLoginTests.post

    def setUp(self):
        guest_fixture.GuestLoginTests.setUp(self)
        AiDigest.__table__.create(db.engine)
        now = datetime.utcnow() - timedelta(minutes=1)
        db.session.add(AiDigest(id=6, issue_date=now.date(), timezone='Asia/Shanghai', slug='test',
            title='测试快讯', summary='测试', content_json='{}', content_version=1, status='published',
            source_window_start=now, source_window_end=now, scheduled_publish_at=now,
            published_at=now, created_at=now, updated_at=now))
        db.session.commit()
        self.headers = {'Authorization': self.regular.generate_auth_token(3600)}
        self.admin_headers = {'Authorization': self.owner.generate_auth_token(3600)}
        self.upload_patch = patch('app.api.media.put_image')
        self.upload_transport = self.upload_patch.start()
        self.delete_patch = patch('app.media.delete_image')
        self.delete_transport = self.delete_patch.start()

    def tearDown(self):
        self.upload_patch.stop()
        self.delete_patch.stop()
        guest_fixture.GuestLoginTests.tearDown(self)

    def png(self):
        output = io.BytesIO()
        Image.new('RGB', (20, 10), '#123456').save(output, format='PNG')
        return output.getvalue()

    def upload(self, headers=None):
        response = self.client.post('/api/media/images/', headers=headers or self.headers,
            data={'image': (io.BytesIO(self.png()), 'image.png')})
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()

    def message(self, body, **kwargs):
        response = self.client.post('/api/add-message/', headers=self.headers, json=dict(body=body, **kwargs))
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def test_digest_comments_share_visibility_moderation_and_admin_list(self):
        result = self.client.post('/api/add-comment/', headers=self.headers, json={'body': '快讯评论', 'digest_id': 6})
        self.assertEqual(result.status_code, 200, result.get_json())
        comment = result.get_json()['comments'][0]
        self.assertEqual(comment['digest_id'], 6)
        self.assertIsNone(comment['post_id'])
        self.assertEqual(comment['target_url'], '/ai-news/6')
        self.assertEqual(comment['post_title'], '测试快讯')
        self.assertTrue(comment['hide'])
        self.assertEqual(self.client.get('/api/comments/?digest_id=6').get_json()['comment_times'], 0)
        self.assertEqual(self.client.get('/api/comments/?digest_id=6', headers=self.headers).get_json()['comment_times'], 1)
        moderation = self.client.post('/api/get-comments/', headers=self.admin_headers, json={}).get_json()
        self.assertEqual(moderation['list'][0]['digest_id'], 6)
        self.assertEqual(self.client.get('/api/set-comment-show/{}'.format(comment['id']), headers=self.admin_headers).status_code, 200)
        self.assertEqual(self.client.get('/api/comments/?digest_id=6').get_json()['comment_times'], 1)
        AiDigest.query.get(6).status = 'draft'
        db.session.commit()
        self.assertEqual(self.client.get('/api/comments/?digest_id=6').status_code, 404)
        self.assertEqual(self.client.post('/api/add-comment/', headers=self.headers, json={'body': 'draft', 'digest_id': 6}).status_code, 404)

    def test_cross_target_reply_and_hidden_reply_are_rejected(self):
        root = Comment(body='other', post_id=self.post_id, author_id=self.owner_id, hide=False)
        private = Comment(body='private', digest_id=6, author_id=self.owner_id, hide=True)
        db.session.add_all([root, private])
        db.session.commit()
        ids = [root.id, private.id]
        for response_id in ids:
            response = self.client.post('/api/add-comment/', headers=self.headers,
                json={'body': 'reply', 'digest_id': 6, 'response_id': response_id})
            self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client.get('/api/comments/?digest_id=6&post_id=1').status_code, 404)

    def test_script_and_protocol_injection_is_rejected_and_plain_links_work(self):
        for body in ('<script>alert(1)</script>', '<img src=x onerror=alert(1)>',
                     '[点我](javascript:alert(1))', '[x](data:text/html,evil)',
                     '[x](vbscript:foo)', '![x](https://outside.example/image.png)',
                     '[x](https://user:pass@example.test)', '<iframe src="https://example.test">'):
            self.assertEqual(self.client.post('/api/add-message/', headers=self.headers, json={'body': body}).status_code, 400)
            self.assertEqual(self.client.post('/api/add-comment/', headers=self.headers, json={'body': body, 'digest_id': 6}).status_code, 400)
        body = "2 < 3，**加粗**和[链接](https://example.test/path?q=1)，SQL 字符串 ' OR 1=1 --"
        self.assertEqual(self.message(body)['body'], body)
        self.assertEqual(User.query.count(), 2)

    def test_upload_requires_auth_and_checks_bytes_format_pixels_and_metadata(self):
        self.assertEqual(self.client.post('/api/media/images/', data={'image': (io.BytesIO(self.png()), 'x.png')}).status_code, 401)
        for data, name in ((b'<svg onload="alert(1)"></svg>', 'fake.png'), (b'not image', 'fake.jpg'), (self.png()[:20], 'broken.png')):
            response = self.client.post('/api/media/images/', headers=self.headers, data={'image': (io.BytesIO(data), name)})
            self.assertEqual(response.status_code, 400)
        response = self.client.post('/api/media/images/', headers=self.headers, data={'image': (io.BytesIO(b'x' * (5 * 1024 * 1024 + 1)), 'big.png')})
        self.assertIn(response.status_code, (400, 413))
        with patch('app.media.MAX_IMAGE_PIXELS', 10):
            with self.assertRaises(ValueError):
                prepare_image(self.png())
        data, _, mime, width, height = prepare_image(self.png() + b'<script>trailing payload</script>')
        self.assertNotIn(b'<script>', data)
        self.assertEqual((mime, width, height), ('image/jpeg', 20, 10))
        self.assertEqual(MediaAsset.query.count(), 0)

    def test_image_reference_and_text_first_order_are_saved(self):
        first, second = self.upload(), self.upload()
        body = '![第一张]({})\n文字 [链接](https://example.test)\n![第二张]({})'.format(first['url'], second['url'])
        row = self.message(body)
        self.assertTrue(row['body'].startswith('\n文字 [链接]'))
        self.assertLess(row['body'].index('文字'), row['body'].index('![第一张]'))
        self.assertLess(row['body'].index(first['url']), row['body'].index(second['url']))
        self.assertEqual(MediaReference.query.count(), 2)
        self.assertIsNone(MediaAsset.query.get(first['id']).delete_after)
        self.delete_transport.assert_not_called()

    def test_cannot_reference_another_users_image_or_nonexistent_asset(self):
        other = self.upload(self.admin_headers)
        for url in (other['url'], 'https://example.test/managed-images/' + 'a' * 32 + '.png'):
            response = self.client.post('/api/add-message/', headers=self.headers, json={'body': '![图片]({})'.format(url)})
            self.assertEqual(response.status_code, 400)
        self.assertEqual(Message.query.count(), 0)
        self.assertEqual(MediaReference.query.count(), 0)

    def test_edit_removes_last_reference_and_physically_deletes_image(self):
        picture = self.upload()
        row = self.message('文字\n![图片]({})'.format(picture['url']))
        response = self.client.put('/api/messages/{}/'.format(row['id']), headers=self.headers, json={'body': '只保留文字'})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(MediaReference.query.count(), 0)
        self.assertEqual(MediaAsset.query.get(picture['id']).status, 'deleted')
        self.delete_transport.assert_called_once()

    def test_shared_image_survives_until_last_content_is_deleted(self):
        picture = self.upload()
        one = self.message('一\n![图]({})'.format(picture['url']))
        two = self.message('二\n![图]({})'.format(picture['url']))
        response = self.client.get('/api/delete-message/{}'.format(one['id']), headers=self.admin_headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(MediaReference.query.count(), 1)
        self.delete_transport.assert_not_called()
        self.assertEqual(MediaAsset.query.get(picture['id']).status, 'ready')
        self.client.get('/api/delete-message/{}'.format(two['id']), headers=self.admin_headers)
        self.assertEqual(MediaReference.query.count(), 0)
        self.delete_transport.assert_called_once()

    def test_reorder_keeps_all_files_and_saves_order(self):
        one, two = self.upload(), self.upload()
        row = self.message('文字\n![一]({})\n![二]({})'.format(one['url'], two['url']))
        response = self.client.put('/api/messages/{}/'.format(row['id']), headers=self.headers,
            json={'body': '文字\n![二]({})\n![一]({})'.format(two['url'], one['url'])})
        self.assertEqual(response.status_code, 200)
        self.assertLess(response.get_json()['body'].index(two['url']), response.get_json()['body'].index(one['url']))
        self.assertEqual(MediaReference.query.count(), 2)
        self.delete_transport.assert_not_called()

    def test_abandoned_uploads_wait_24_hours_and_cleanup_failures_retry(self):
        asset = self.upload()
        self.assertEqual(cleanup_images(now=datetime.utcnow() + timedelta(hours=23)), 0)
        self.delete_transport.side_effect = RuntimeError('storage temporarily unavailable')
        future = datetime.utcnow() + timedelta(hours=25)
        self.assertEqual(cleanup_images(now=future), 0)
        self.assertEqual(MediaAsset.query.get(asset['id']).status, 'deleting')
        self.delete_transport.side_effect = None
        self.assertEqual(cleanup_images(now=future + timedelta(minutes=6)), 1)
        self.assertEqual(MediaAsset.query.get(asset['id']).status, 'deleted')

    def test_content_rollback_does_not_drop_references_or_files(self):
        asset = self.upload()
        row = self.message('文字\n![图]({})'.format(asset['url']))
        message = Message.query.get(row['id'])
        message.body = 'temporary change'
        db.session.flush()
        db.session.rollback()
        self.assertEqual(MediaReference.query.count(), 1)
        self.assertEqual(cleanup_images(now=datetime.utcnow() + timedelta(days=2)), 0)
        self.delete_transport.assert_not_called()

    def test_deleting_comment_subtree_cleans_images_in_replies(self):
        asset = self.upload()
        root = Comment(body='root', digest_id=6, author_id=self.regular_id, hide=False)
        db.session.add(root)
        db.session.flush()
        reply = Comment(body='![图]({})'.format(asset['url']), digest_id=6, author_id=self.regular_id, response_id=root.id)
        db.session.add(reply)
        db.session.commit()
        root_id = root.id
        response = self.client.get('/api/delete-comment/{}'.format(root_id), headers=self.admin_headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Comment.query.count(), 0)
        self.assertEqual(MediaReference.query.count(), 0)
        self.delete_transport.assert_called_once()

    def test_six_image_limit_and_failed_upload_tracking(self):
        picture = self.upload()
        with self.assertRaises(ValueError):
            validate_body(('![图]({})\n'.format(picture['url'])) * 7)
        self.upload_transport.side_effect = RuntimeError('upload failed')
        response = self.client.post('/api/media/images/', headers=self.headers, data={'image': (io.BytesIO(self.png()), 'image.png')})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(MediaAsset.query.filter_by(status='orphan').count(), 1)

    def test_migration_preserves_article_comments_and_reverses(self):
        path = Path(__file__).parents[1] / 'migrations/versions/20260917_rich_comments.py'
        spec = importlib.util.spec_from_file_location('rich_migration', path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        engine = create_engine('sqlite://')
        with engine.begin() as connection:
            connection.execute(text('CREATE TABLE comments (id INTEGER PRIMARY KEY, body TEXT, post_id INTEGER)'))
            connection.execute(text('CREATE TABLE ai_digests (id INTEGER PRIMARY KEY)'))
            connection.execute(text("INSERT INTO comments (id, body, post_id) VALUES (1, 'old', 2)"))
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                self.assertEqual(tuple(connection.execute(text('SELECT body, post_id, digest_id FROM comments')).first()), ('old', 2, None))
                self.assertEqual(set(db.metadata.tables['media_assets'].columns.keys()), {row['name'] for row in inspect(connection).get_columns('media_assets')})
                migration.downgrade()
                migration.upgrade()
        engine.dispose()

    def test_legacy_image_index_is_non_destructive_and_preserves_shared_references(self):
        from media_admin import index_existing
        key = 'd' * 32
        self.post.body_html = '<p>旧文章</p><img src="https://example.test/{}">'.format(key)
        self.regular.avatar = key
        db.session.commit()
        preview = index_existing(apply=False)
        self.assertEqual(preview['new_images'], 1)
        self.assertEqual(MediaAsset.query.count(), 0)
        result = index_existing(apply=True)
        self.assertEqual(result['new_references'], 2)
        self.assertEqual(index_existing(apply=True)['new_images'], 0)
        self.post.body_html = '移除旧图'
        db.session.commit()
        self.assertEqual(cleanup_images(now=datetime.utcnow() + timedelta(days=1)), 0)
        self.regular.avatar = 'default'
        db.session.commit()
        self.assertEqual(cleanup_images(), 1)
        self.delete_transport.assert_called_once_with(key)
