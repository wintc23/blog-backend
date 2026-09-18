"""Likes and quick guest identities, using isolated SQLite and mocked mail only."""
import importlib.util
from pathlib import Path
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
import unittest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import IntegrityError
import test_guest_login as guest_fixture
from app import db
from app.models import Like, User, GuestRateLimit
from app.digest_models import AiDigest, AiDigestLike


class LikeTests(unittest.TestCase):
    owner = guest_fixture.GuestLoginTests.owner
    regular = guest_fixture.GuestLoginTests.regular
    post = guest_fixture.GuestLoginTests.post
    guest = guest_fixture.GuestLoginTests.guest
    login = guest_fixture.GuestLoginTests.login

    def setUp(self):
        guest_fixture.GuestLoginTests.setUp(self)
        AiDigest.__table__.create(db.engine)
        path = Path(__file__).parents[1] / 'migrations/versions/20260918_digest_likes.py'
        spec = importlib.util.spec_from_file_location('like_migration', path)
        self.migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.migration)
        with db.engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                self.migration.upgrade()
        self.digest_id = self.issue()
        self.headers = {'Authorization': self.regular.generate_auth_token(3600)}

    def tearDown(self):
        guest_fixture.GuestLoginTests.tearDown(self)

    def issue(self, status='published', future=False):
        now = datetime.utcnow() - timedelta(minutes=5)
        day = now.date() - timedelta(days=AiDigest.query.count())
        published = now + timedelta(days=1) if future else now
        row = AiDigest(issue_date=day, timezone='Asia/Shanghai', slug=day.isoformat(), title='快讯',
            summary='测试', content_json='{}', status=status, source_window_start=now, source_window_end=now,
            scheduled_publish_at=published, published_at=published if status == 'published' else None,
            created_at=now, updated_at=now)
        db.session.add(row)
        db.session.commit()
        return row.id

    def paths(self):
        return ['/api/posts/{}/likes/'.format(self.post_id), '/api/ai-digests/{}/likes/'.format(self.digest_id)]

    def test_public_reads_do_not_create_users_or_likes_and_are_not_cached(self):
        for path in self.paths():
            response = self.client.get(path)
            self.assertEqual(response.get_json(), {'likes': 0, 'like': False})
            self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertEqual(User.query.count(), 2)
        self.assertEqual(AiDigestLike.query.count(), 0)
        self.assertEqual(Like.query.count(), 0)

    def test_anonymous_and_invalid_tokens_cannot_write(self):
        for path in self.paths():
            for headers in ({}, {'Authorization': 'expired-token'}):
                for method in (self.client.post, self.client.delete):
                    self.assertEqual(method(path, headers=headers).status_code, 401)
            self.assertEqual(self.client.get(path, headers={'Authorization': 'expired-token'}).status_code, 401)

    def test_guest_like_persists_reloads_and_cancels_idempotently(self):
        user, headers = self.guest()
        for path in self.paths():
            for _ in range(2):
                response = self.client.post(path, headers=headers)
                self.assertEqual(response.status_code, 200, response.get_json())
                self.assertEqual(response.get_json()['likes'], 1)
                self.assertTrue(response.get_json()['like'])
            self.assertEqual(self.client.get(path, headers=headers).get_json(), {'likes': 1, 'like': True})
            self.assertEqual(self.client.get(path).get_json(), {'likes': 1, 'like': False})
            for _ in range(2):
                self.assertEqual(self.client.delete(path, headers=headers).get_json(), {'likes': 0, 'like': False})
        self.assertEqual(User.query.count(), 3)

    def test_users_have_independent_likes(self):
        _, guest = self.guest()
        for path in self.paths():
            self.client.post(path, headers=guest)
            self.assertEqual(self.client.get(path, headers=self.headers).get_json(), {'likes': 1, 'like': False})
            self.assertEqual(self.client.post(path, headers=self.headers).get_json(), {'likes': 2, 'like': True})
            self.assertEqual(self.client.delete(path, headers=guest).get_json(), {'likes': 1, 'like': False})
            self.assertTrue(self.client.get(path, headers=self.headers).get_json()['like'])

    def test_private_and_future_content_cannot_be_liked(self):
        ids = [self.issue('draft'), self.issue('withdrawn'), self.issue(future=True), 99999]
        for digest_id in ids:
            path = '/api/ai-digests/{}/likes/'.format(digest_id)
            for headers in (self.headers, {'Authorization': self.owner.generate_auth_token(3600)}):
                for method in (self.client.get, self.client.post, self.client.delete):
                    self.assertEqual(method(path, headers=headers).status_code, 404)
        self.post.hide = True
        db.session.commit()
        self.assertEqual(self.client.post(self.paths()[0], headers=self.headers).status_code, 404)
        self.assertEqual(AiDigestLike.query.count(), 0)

    def test_guest_blog_and_digest_likes_share_rate_limits(self):
        _, headers = self.guest()
        post_path, digest_path = self.paths()
        self.assertEqual(self.client.post(post_path, headers=headers).status_code, 200)
        for _ in range(4):
            self.assertEqual(self.client.post(digest_path, headers=headers).status_code, 200)
            self.assertEqual(self.client.delete(digest_path, headers=headers).status_code, 200)
        self.assertEqual(self.client.post(digest_path, headers=headers).status_code, 429)
        self.assertEqual(AiDigestLike.query.count(), 0)

    def test_concurrent_digest_likes_are_unique(self):
        path = self.paths()[1]
        def like(_):
            return self.app.test_client().post(path, headers=self.headers).status_code
        with ThreadPoolExecutor(max_workers=4) as workers:
            statuses = list(workers.map(like, range(4)))
        self.assertEqual(statuses, [200] * 4)
        self.assertEqual(AiDigestLike.query.count(), 1)
        self.assertEqual(self.client.get(path, headers=self.headers).get_json(), {'likes': 1, 'like': True})

    def test_database_rejects_duplicate_likes(self):
        db.session.add(AiDigestLike(digest_id=self.digest_id, author_id=self.regular_id))
        db.session.commit()
        db.session.add(AiDigestLike(digest_id=self.digest_id, author_id=self.regular_id))
        with self.assertRaises(IntegrityError):
            db.session.commit()
        db.session.rollback()
