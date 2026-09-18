"""Email authentication uses an isolated database and an in-memory mail transport."""
import importlib.util
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

os.environ.setdefault('FLASK_POSTS_PER_PAGE', '10')
os.environ.setdefault('FLASK_BBS_PER_PAGE', '10')

from alembic.migration import MigrationContext
from alembic.operations import Operations
from itsdangerous import TimedJSONWebSignatureSerializer as Serializer
from sqlalchemy import create_engine, text
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from app import create_app, db
from app.email_login import send_login_code
from app.models import (User, Role, Permission, GuestRateLimit, EmailLoginChallenge,
                        PersonalProfile, PostType, Post, Comment, Message, Like, StatEvent)


@compiles(MEDIUMTEXT, 'sqlite')
def mediumtext_sqlite(type_, compiler, **kwargs):
    return 'TEXT'


class EmailLoginTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory(prefix='email-login-test-')
        self.app = create_app('testing')
        self.app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///' + self.directory.name + '/test.sqlite',
            SECRET_KEY='isolated-email-test', QI_NIU_LINK_URL='https://files.example.test',
            DOMAIN='https://example.test', MAIL_SERVER='smtp.example.test', MAIL_USERNAME='sender@example.test',
            MAIL_PASSWORD='fake', MAIL_SENDER='sender@example.test', MAIL_USE_SSL=True)
        self.context = self.app.app_context()
        self.context.push()
        from app.media_models import MediaAsset, MediaReference
        MediaAsset.__table__.create(db.engine)
        MediaReference.__table__.create(db.engine)
        for model in (Role, User, GuestRateLimit, EmailLoginChallenge, PersonalProfile,
                      PostType, Post, Comment, Message, Like, StatEvent):
            model.__table__.create(db.engine)
        Role.insert_roles()
        db.session.add(PersonalProfile(id=1, site_name='数据库中的测试站名'))
        db.session.commit()
        self.client = self.app.test_client()
        self.clock_patch = patch('app.guest._now', return_value=1800000000)
        self.clock = self.clock_patch.start()
        self.mail_patch = patch('app.api.email_login.send_login_code')
        self.mail = self.mail_patch.start()

    def tearDown(self):
        self.mail_patch.stop()
        self.clock_patch.stop()
        db.session.remove()
        db.engine.dispose()
        self.context.pop()
        self.directory.cleanup()

    def issue(self, email='reader@example.test'):
        response = self.client.post('/api/email-login/code/', json={'email': email})
        self.assertEqual(response.status_code, 200, response.get_json())
        return dict(email=email, challenge_id=response.get_json()['challenge_id'], code=self.mail.call_args[0][1])

    def verify(self, data, **kwargs):
        return self.client.post('/api/email-login/', json=data, **kwargs)

    def create_user(self, email='reader@example.test', provider='qq', role='User'):
        user = User(id_string=provider + ':test', username='保留昵称', avatar='avatar-key', email=email,
                    role=Role.query.filter_by(name=role).one())
        db.session.add(user)
        db.session.commit()
        return user.id, user.generate_auth_token(3600)

    def test_new_email_creates_one_random_profile_and_code_is_single_use(self):
        challenge = self.issue(' Reader@Example.Test ')
        stored = EmailLoginChallenge.query.get(challenge['challenge_id'])
        self.assertNotEqual(stored.code_digest, challenge['code'])
        self.assertEqual(len(stored.code_digest), 64)
        self.assertEqual(self.mail.call_args[0][0], 'reader@example.test')
        response = self.verify(challenge)
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data['user']['email'], 'reader@example.test')
        self.assertRegex(data['user']['username'], r'^用户\d{8}$')
        self.assertTrue(data['user']['avatar'].startswith('data:image/svg+xml;'))
        self.assertFalse(data['user']['admin'])
        self.assertFalse(data['user']['is_guest'])
        self.assertIsNotNone(User.query.get(data['user']['id']).email_verified_at)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(self.verify(challenge).status_code, 400)
        self.assertEqual(User.query.count(), 1)
        self.assertEqual(self.client.get('/api/get-self/', headers={'Authorization': data['token']}).status_code, 200)
        self.assertNotIn('email', self.client.get('/api/get-user/{}'.format(data['user']['id'])).get_json())

    def test_guest_email_save_needs_no_code_and_recovery_keeps_content_and_revokes_old_token(self):
        guest = self.client.post('/api/guest-login/', json={}).get_json()
        user_id = guest['user']['id']
        legacy_token = Serializer(self.app.config['SECRET_KEY'], expires_in=3600).dumps({'id': user_id}).decode()
        headers = {'Authorization': guest['token']}
        response = self.client.post('/api/set-email/', headers=headers, json={'user_id': user_id, 'email': 'reader@example.test'})
        self.assertEqual(response.status_code, 200)
        self.mail.assert_not_called()
        self.assertIsNone(User.query.get(user_id).email_verified_at)
        db.session.add(Message(body='原来的留言', author_id=user_id, hide=True))
        # Even a misconfigured guest role must not become an administrator.
        User.query.get(user_id).role = Role.query.filter_by(name='Administrator').one()
        db.session.commit()
        recovered = self.verify(self.issue()).get_json()
        self.assertEqual(recovered['user']['id'], user_id)
        self.assertEqual(recovered['user']['username'], guest['user']['username'])
        self.assertEqual(recovered['user']['avatar'], guest['user']['avatar'])
        self.assertEqual(Message.query.one().author_id, user_id)
        self.assertFalse(recovered['user']['admin'])
        self.assertFalse(recovered['user']['is_guest'])
        for old_token in (guest['token'], legacy_token):
            self.assertEqual(self.client.get('/api/get-self/', headers={'Authorization': old_token}).status_code, 401)
        self.assertEqual(self.client.get('/api/get-self/', headers={'Authorization': recovered['token']}).status_code, 200)

    def test_existing_oauth_and_legacy_tokens_remain_compatible(self):
        user_id, token = self.create_user(email='Reader@Example.Test', role='Administrator')
        legacy = Serializer(self.app.config['SECRET_KEY'], expires_in=3600).dumps({'id': user_id}).decode()
        result = self.verify(self.issue()).get_json()
        self.assertEqual(result['user']['id'], user_id)
        self.assertEqual(result['user']['username'], '保留昵称')
        self.assertTrue(result['user']['admin'])
        self.assertEqual(User.query.get(user_id).id_string, 'qq:test')
        for original in (token, legacy):
            self.assertEqual(self.client.get('/api/get-self/', headers={'Authorization': original}).status_code, 200)

    def test_wrong_expired_email_mismatch_and_exhausted_codes_fail(self):
        code = self.issue()
        wrong = '000000' if code['code'] != '000000' else '111111'
        self.assertEqual(self.verify(dict(code, email='other@example.test')).status_code, 400)
        for _ in range(5):
            self.assertEqual(self.verify(dict(code, code=wrong)).status_code, 400)
        self.assertEqual(self.verify(code).status_code, 400)
        self.assertEqual(EmailLoginChallenge.query.get(code['challenge_id']).attempts, 5)
        self.clock.return_value += 61
        replacement = self.issue()
        self.clock.return_value += 300
        self.assertEqual(self.verify(replacement).status_code, 400)
        self.assertEqual(User.query.count(), 0)

    def test_resend_invalidates_old_code_and_enforces_cooldown(self):
        old = self.issue()
        response = self.client.post('/api/email-login/code/', json={'email': old['email']})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers['Retry-After'], '60')
        self.assertEqual(self.mail.call_count, 1)
        self.clock.return_value += 61
        new = self.issue()
        self.assertEqual(self.verify(old).status_code, 400)
        self.assertEqual(self.verify(new).status_code, 200)

    def test_cumulative_verification_limit_survives_resends_and_ip_changes(self):
        first = self.issue()
        for _ in range(30):
            self.assertEqual(self.verify(dict(first, challenge_id='0' * 48)).status_code, 400)
        self.clock.return_value += 61
        new = self.issue()
        response = self.verify(new, environ_base={'REMOTE_ADDR': '203.0.113.22'})
        self.assertEqual(response.status_code, 429)
        self.assertGreater(int(response.headers['Retry-After']), 0)
        self.assertEqual(User.query.count(), 0)

    def test_parallel_verification_only_issues_one_session(self):
        code = self.issue()
        def verify(_):
            return self.app.test_client().post('/api/email-login/', json=code).status_code
        with ThreadPoolExecutor(max_workers=4) as pool:
            statuses = list(pool.map(verify, range(4)))
        self.assertEqual(statuses.count(200), 1)
        self.assertEqual(statuses.count(400), 3)
        self.assertEqual(User.query.count(), 1)

    def test_parallel_sends_only_deliver_once(self):
        def send(_):
            return self.app.test_client().post('/api/email-login/code/', json={'email': 'reader@example.test'}).status_code
        with ThreadPoolExecutor(max_workers=4) as pool:
            statuses = list(pool.map(send, range(4)))
        self.assertEqual(statuses.count(200), 1)
        self.assertEqual(statuses.count(429), 3)
        self.assertEqual(self.mail.call_count, 1)

    def test_email_change_invalidates_code_even_if_changed_back(self):
        user_id, token = self.create_user()
        User.query.get(user_id).email_verified_at = datetime.utcnow()
        db.session.commit()
        code = self.issue()
        for email in ('new@example.test', 'reader@example.test'):
            response = self.client.post('/api/set-email/', json={'user_id': user_id, 'email': email}, headers={'Authorization': token})
            self.assertEqual(response.status_code, 200)
        self.assertIsNone(User.query.get(user_id).email_verified_at)
        self.assertEqual(self.verify(code).status_code, 400)
        self.assertEqual(self.client.get('/api/get-self/', headers={'Authorization': token}).status_code, 200)

    def test_code_cannot_follow_email_to_another_account(self):
        code = self.issue()
        self.create_user()
        self.assertEqual(self.verify(code).status_code, 400)

    def test_smtp_failure_never_returns_success_or_a_usable_challenge(self):
        self.mail.side_effect = RuntimeError('simulated failure')
        response = self.client.post('/api/email-login/code/', json={'email': 'reader@example.test'})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()['retry_after'], 60)
        record = EmailLoginChallenge.query.one()
        self.assertFalse(record.ready)
        self.assertIsNotNone(record.consumed_at)
        self.assertNotIn('challenge_id', response.get_json())

    def test_mail_contains_account_database_site_name_code_and_expiry(self):
        with patch('app.email_login.smtplib.SMTP_SSL') as transport:
            transport.return_value.__enter__.return_value.send_message.return_value = {}
            send_login_code('reader@example.test', '012345')
            message = transport.return_value.__enter__.return_value.send_message.call_args[0][0]
            for body in (message.get_body(preferencelist=('plain',)).get_content(), message.get_body(preferencelist=('html',)).get_content()):
                for expected in ('reader@example.test', '数据库中的测试站名', '012345', '5 分钟'):
                    self.assertIn(expected, body)
            self.assertIn('数据库中的测试站名', str(message['Subject']))
            self.assertEqual(transport.call_args[1]['timeout'], 10)
            self.assertTrue(transport.call_args[1]['context'].check_hostname)

    def test_invalid_payloads_never_send_or_create_users(self):
        for email in ('not-email', 'a\r\nb@example.test', 'x' * 65 + '@example.test', None, [], {}):
            response = self.client.post('/api/email-login/code/', json={'email': email})
            self.assertEqual(response.status_code, 400)
        for data in ([], None, {}, {'email': 'a@example.test', 'code': 123456}):
            self.assertEqual(self.verify(data).status_code, 400)
        self.mail.assert_not_called()
        self.assertEqual(User.query.count(), 0)

    def test_migration_preserves_existing_data_and_defaults(self):
        path = Path(__file__).parents[1] / 'migrations/versions/20260917_email_login.py'
        spec = importlib.util.spec_from_file_location('email_migration', path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        engine = create_engine('sqlite:///' + self.directory.name + '/migration.sqlite')
        with engine.begin() as connection:
            connection.execute(text('CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR(64))'))
            connection.execute(text("INSERT INTO users (id, email) VALUES (1, 'existing@example.test')"))
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                row = connection.execute(text('SELECT email, email_version, auth_version, email_verified_at FROM users')).first()
                self.assertEqual(tuple(row), ('existing@example.test', 0, 0, None))
                migration.downgrade()
                migration.upgrade()
        engine.dispose()
