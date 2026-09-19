"""Guest authentication, ownership and throttling against isolated SQLite only."""
import importlib.util
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import patch
from urllib.parse import unquote

os.environ.setdefault('FLASK_POSTS_PER_PAGE', '10')
os.environ.setdefault('FLASK_BBS_PER_PAGE', '10')

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from app import create_app, db
from app.models import User, Role, Permission, GuestRateLimit, PostType, Post, Comment, Message, Like, StatEvent
from app.guest import client_address


@compiles(MEDIUMTEXT, 'sqlite')
def mediumtext_sqlite(type_, compiler, **kwargs):
    return 'TEXT'


class GuestLoginTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app('testing')
        self.database = TemporaryDirectory(prefix='guest-login-test-')
        self.app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///' + self.database.name + '/test.sqlite', SECRET_KEY='isolated-test-secret',
                               QI_NIU_LINK_URL='https://example.test', DOMAIN='https://example.test')
        self.context = self.app.app_context()
        self.context.push()
        from app.media_models import MediaAsset, MediaReference
        MediaAsset.__table__.create(db.engine)
        MediaReference.__table__.create(db.engine)
        for model in (Role, User, GuestRateLimit, PostType, Post, Comment, Message, Like, StatEvent):
            model.__table__.create(db.engine)
        from app.interaction_notifications import InteractionNotification
        InteractionNotification.__table__.create(db.engine)
        Role.insert_roles()
        owner = User(id_string='github-owner', username='owner', role=Role.query.filter_by(name='Administrator').one())
        regular = User(id_string='qq-user', username='member')
        category = PostType(name='文章', alias='article', special=0)
        post = Post(title='公开文章', author=owner, type=category, hide=False)
        db.session.add_all([owner, regular, post])
        db.session.commit()
        self.owner_id, self.regular_id, self.post_id = owner.id, regular.id, post.id
        self.client = self.app.test_client()
        self.clock = patch('app.guest._now', return_value=1800000000)
        self.clock.start()
        self.patches = []
        for module in ('comments', 'messages', 'posts'):
            for name in (('send_email', 'notify') if module != 'posts' else ('send_email',)):
                mock = patch('app.api.' + module + '.' + name, return_value=False)
                mock.start()
                self.patches.append(mock)

    def tearDown(self):
        for mock in self.patches:
            mock.stop()
        self.clock.stop()
        db.session.remove()
        db.engine.dispose()
        self.context.pop()
        self.database.cleanup()

    def login(self, **kwargs):
        return self.client.post('/api/guest-login/', json={}, **kwargs)

    @property
    def owner(self):
        return User.query.get(self.owner_id)

    @property
    def regular(self):
        return User.query.get(self.regular_id)

    @property
    def post(self):
        return Post.query.get(self.post_id)

    def guest(self, **kwargs):
        response = self.login(**kwargs)
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        return data['user'], {'Authorization': data['token']}

    def test_generated_profile_is_persisted_and_cannot_be_chosen_by_client(self):
        response = self.client.post('/api/guest-login/', json={
            'user_id': self.owner.id, 'username': 'owner', 'role_id': self.owner.role_id,
            'admin': True, 'avatar': 'https://untrusted.test/track', 'email': 'spam@example.test'})
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        user = data['user']
        self.assertRegex(user['username'], r'^用户\d{8}$')
        self.assertTrue(user['is_guest'])
        self.assertFalse(user['admin'])
        self.assertIsNone(user['email'])
        self.assertNotEqual(user['id'], self.owner.id)
        self.assertTrue(User.query.get(user['id']).id_string.startswith('guest:'))
        self.assertNotIn('id_string', user)
        svg = unquote(user['avatar'].split(',', 1)[1])
        self.assertTrue(svg.startswith('<svg '))
        self.assertNotIn('script', svg)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        headers = {'Authorization': data['token']}
        self.assertEqual(self.client.get('/api/get-self/', headers=headers).get_json(), user)
        public_user = dict(user)
        public_user.pop('email')
        self.assertEqual(self.client.get('/api/get-user/{0}'.format(user['id'])).get_json(), public_user)

    def test_valid_session_reuses_identity_without_consuming_signup_quota(self):
        user, headers = self.guest()
        for _ in range(5):
            self.assertEqual(self.login(headers=headers).get_json()['user'], user)
        self.assertEqual(User.query.count(), 3)
        self.assertTrue(all(row.hits == 1 for row in GuestRateLimit.query.all()))
        # A normal user's authenticated session must not become a guest.
        headers = {'Authorization': self.regular.generate_auth_token(3600)}
        self.assertEqual(self.login(headers=headers).get_json()['user']['id'], self.regular.id)

    def test_signup_limits_are_shared_and_expire(self):
        for _ in range(3):
            self.guest()
        other_client = self.app.test_client()
        response = other_client.post('/api/guest-login/', json={}, headers={'X-Forwarded-For': '203.0.113.99'})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers['Retry-After'], '60')
        self.assertEqual(User.query.count(), 5)
        with patch('app.guest._now', return_value=1800000061):
            self.guest()
        self.assertEqual(User.query.count(), 6)

    def test_daily_limit_cannot_be_bypassed_by_waiting_out_minute(self):
        for i in range(10):
            with patch('app.guest._now', return_value=1800000000 + i * 61):
                self.guest()
        with patch('app.guest._now', return_value=1800001000):
            self.assertEqual(self.login().status_code, 429)
        with patch('app.guest._now', return_value=1800086401):
            self.guest()

    def test_concurrent_requests_cannot_overrun_signup_limit(self):
        def signup(_):
            return self.app.test_client().post('/api/guest-login/', json={}).status_code
        with ThreadPoolExecutor(max_workers=6) as workers:
            statuses = list(workers.map(signup, range(8)))
        self.assertEqual(statuses.count(200), 3)
        self.assertEqual(statuses.count(429), 5)
        self.assertEqual(User.query.count(), 5)

    def test_proxy_headers_are_only_trusted_from_loopback(self):
        with self.app.test_request_context('/', environ_base={'REMOTE_ADDR': '203.0.113.5'},
                                           headers={'X-Real-IP': '198.51.100.6'}):
            self.assertEqual(client_address(), '203.0.113.5')
        with self.app.test_request_context('/', environ_base={'REMOTE_ADDR': '127.0.0.1'},
                                           headers={'X-Real-IP': '198.51.100.6'}):
            self.assertEqual(client_address(), '198.51.100.6')

    def test_guest_has_no_management_or_registered_account_privileges(self):
        user, headers = self.guest()
        for path, body in [('/api/search-user/', {}), ('/api/get-comments/', {}),
                           ('/api/add-link/', {'title': 'spam'}), ('/api/update-link/', {'id': 1})]:
            self.assertEqual(self.client.post(path, json=body, headers=headers).status_code, 403)
        self.assertEqual(self.client.get('/api/check-admin/', headers=headers).get_json(), {'admin': False})
        row = User.query.get(user['id'])
        row.role = self.owner.role
        db.session.commit()
        self.assertFalse(row.can(Permission.ADMIN))
        self.assertFalse(row.can(Permission.WRITE))
        self.assertFalse(row.can(Permission.MODERATE))
        self.assertEqual(self.client.get('/api/check-admin/', headers=headers).get_json(), {'admin': False})

    def test_guest_sets_notification_email_without_verification_and_it_stays_private(self):
        user, headers = self.guest()
        data = {'user_id': user['id'], 'email': ' Guest@Example.Test '}
        for _ in range(2):
            self.assertEqual(self.client.post('/api/set-email/', json=data, headers=headers).status_code, 200)
        self.assertEqual(self.client.get('/api/get-self/', headers=headers).get_json()['email'], 'guest@example.test')
        self.assertTrue(self.client.get('/api/get-self/', headers=headers).get_json()['is_guest'])
        for path in ('/api/get-user/{}', '/api/get-user-detail/{}'):
            path = path.format(user['id'])
            self.assertNotIn('email', self.client.get(path).get_json())
            self.assertEqual(self.client.get(path, headers=headers).get_json()['email'], 'guest@example.test')
            other_headers = {'Authorization': self.regular.generate_auth_token(3600)}
            self.assertNotIn('email', self.client.get(path, headers=other_headers).get_json())
        self.assertEqual(self.client.post('/api/set-email/', json=dict(data, user_id=self.owner.id), headers=headers).status_code, 403)
        self.assertEqual(self.client.post('/api/set-email/', json=dict(data, email='invalid'), headers=headers).status_code, 400)

    def test_invalid_session_does_not_authenticate_for_writes(self):
        for headers in ({}, {'Authorization': 'forged-token'}):
            self.assertEqual(self.client.post('/api/add-message/', json={'body': 'text'}, headers=headers).status_code, 401)
        self.assertEqual(Message.query.count(), 0)

    def test_comments_and_messages_share_limits_and_keep_moderation(self):
        user, headers = self.guest()
        response = self.client.post('/api/add-comment/', json={'body': '测试评论', 'post_id': self.post.id}, headers=headers)
        self.assertEqual(response.status_code, 200)
        comment = Comment.query.one()
        self.assertEqual(comment.author_id, user['id'])
        self.assertTrue(comment.hide)
        self.assertEqual(self.client.post('/api/add-message/', json={'body': '测试留言'}, headers=headers).status_code, 429)
        self.assertEqual(Message.query.count(), 0)
        with patch('app.guest._now', return_value=1800000016):
            response = self.client.post('/api/add-message/', json={'body': '测试留言'}, headers=headers)
        self.assertEqual(response.status_code, 200)
        msg = Message.query.one()
        self.assertTrue(msg.hide)
        self.assertEqual(msg.author_id, user['id'])
        path = '/api/get-message-detail/{}'.format(msg.id)
        self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.get(path, headers=headers).status_code, 200)

    def test_reply_privacy_and_valid_reply_flow(self):
        user, headers = self.guest()
        root = Message(body='public root', author=self.regular, hide=False)
        db.session.add(root)
        db.session.flush()
        hidden = Message(body='private pending reply', author=self.regular, hide=True,
                         root_response_id=root.id, response_id=root.id)
        db.session.add(hidden)
        db.session.commit()
        root_id, hidden_id = root.id, hidden.id
        path = '/api/get-message-detail/{}'.format(root_id)
        self.assertEqual([m['id'] for m in self.client.get(path, headers=headers).get_json()['list']], [root_id])
        denied = self.client.post('/api/add-message/', json={'body': 'reply', 'response_id': hidden_id}, headers=headers)
        self.assertEqual(denied.status_code, 404)
        response = self.client.post('/api/add-message/', json={'body': 'guest reply', 'response_id': root_id}, headers=headers)
        self.assertEqual(response.status_code, 200)
        reply = response.get_json()
        self.assertTrue(reply['hide'])
        self.assertEqual(reply['author_id'], user['id'])
        self.assertEqual(len(self.client.get(path, headers=headers).get_json()['list']), 2)
        self.assertEqual(len(self.client.get(path).get_json()['list']), 1)

    def test_guest_cannot_comment_hidden_posts_or_reply_across_posts(self):
        _, headers = self.guest()
        self.post.hide = True
        db.session.commit()
        response = self.client.post('/api/add-comment/', json={'body': 'text', 'post_id': self.post.id}, headers=headers)
        self.assertEqual(response.status_code, 404)
        self.post.hide = False
        other = Post(title='other', author=self.owner, type=self.post.type)
        comment = Comment(post=other, author=self.regular, body='other thread', hide=False)
        db.session.add_all([other, comment])
        db.session.commit()
        response = self.client.post('/api/add-comment/', json={'body': 'text', 'post_id': self.post.id, 'response_id': comment.id}, headers=headers)
        self.assertEqual(response.status_code, 404)

    def test_invalid_text_does_not_consume_quota(self):
        _, headers = self.guest()
        for body in (' ', 'x' * 2001, {'not': 'text'}):
            self.assertEqual(self.client.post('/api/add-message/', json={'body': body}, headers=headers).status_code, 400)
        self.assertEqual(self.client.post('/api/add-message/', json={'body': 'valid'}, headers=headers).status_code, 200)

    def test_hourly_post_limit_and_rollback_of_shorter_window(self):
        _, headers = self.guest()
        for i in range(10):
            with patch('app.guest._now', return_value=1800000000 + i * 16):
                self.assertEqual(self.client.post('/api/add-message/', json={'body': 'valid'}, headers=headers).status_code, 200)
        with patch('app.guest._now', return_value=1800000200):
            self.assertEqual(self.client.post('/api/add-message/', json={'body': 'over limit'}, headers=headers).status_code, 429)
        self.assertEqual(Message.query.count(), 10)
        with patch('app.guest._now', return_value=1800003601):
            self.assertEqual(self.client.post('/api/add-message/', json={'body': 'new hour'}, headers=headers).status_code, 200)

    def test_migration_upgrade_and_downgrade(self):
        GuestRateLimit.__table__.drop(db.engine)
        path = Path(__file__).parents[1] / 'migrations/versions/20260917_guest_login.py'
        spec = importlib.util.spec_from_file_location('guest_migration', path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        with db.engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                migration.downgrade()
                migration.upgrade()
        self.guest()
