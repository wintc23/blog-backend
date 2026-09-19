"""Authorization, durable delivery and actual website route integration."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault('FLASK_POSTS_PER_PAGE', '10')
os.environ.setdefault('FLASK_BBS_PER_PAGE', '10')

from app import create_app, db
from app.models import LifeMoment, PersonalProfile, Role, User, Permission, Comment
from lark_bridge.store import Store
from lark_bridge.service import Bridge, accept, image_keys
from lark_bridge.site import Site, redact
from lark_bridge.media import Media
from lark_bridge.configuration import public_links
from lark_bridge.lark import Lark
from lark_bridge.planner import Planner, Cancelled

OWNER = 'ou_owner'


def event(message='om_test', **changes):
    return dict({'type': 'im.message.receive_v1', 'message_id': message, 'chat_id': 'oc_private',
                 'chat_type': 'p2p', 'sender_id': OWNER, 'sender_type': 'user',
                 'create_time': str(int(time.time() * 1000)), 'content': '发布动态'}, **changes)


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.app = create_app('testing')
        self.app.config.update(SQLALCHEMY_DATABASE_URI='sqlite://', SECRET_KEY='test-only-secret', TESTING=True)
        self.context = self.app.app_context()
        self.context.push()
        for table in (Role.__table__, User.__table__, LifeMoment.__table__, PersonalProfile.__table__, Comment.__table__):
            table.create(db.engine)
        # Optional tables introduced by the independently developed media module.
        try:
            from app.media_models import MediaAsset, MediaReference
            for table in (MediaAsset.__table__, MediaReference.__table__):
                table.create(db.engine)
        except ImportError:
            pass
        role = Role(name='Administrator', permissions=31)
        db.session.add(role)
        db.session.flush()
        user = User(id=1, username='owner', role=role)
        db.session.add(user)
        db.session.commit()
        self.env = patch.dict(os.environ, {'LARK_BRIDGE_OWNER_OPEN_ID': OWNER, 'LARK_BRIDGE_APP_ID': 'cli_test',
                                          'LARK_BRIDGE_ADMIN_USER_ID': '1'})
        self.env.start()
        self.lark = Mock()
        self.lark.message.return_value = {'sender': {'id': OWNER, 'sender_type': 'user'}, 'chat_id': 'oc_private',
                                         'body': {'content': '{"text":"发布动态"}'}}
        self.planner = Mock()
        self.bridge = Bridge(self.app, self.store, self.lark, self.planner)
        self.bridge.started = time.time() - 1
        self.payload = {'date': '2026-09-17', 'category': 'daily', 'text': '测试动态',
                        'image_url': 'https://example.com/test.jpg', 'image_alt': '', 'location': ''}

    def tearDown(self):
        self.env.stop()
        db.session.remove()
        db.engine.dispose()
        self.context.pop()
        self.temp.cleanup()

    def action(self, tool, arguments=None, reply='完成'):
        return {'tool': tool, 'arguments': arguments or {}, 'reply': reply}

    def test_non_owner_bot_group_and_old_events_rejected(self):
        for changes in ({'sender_id': 'ou_stranger'}, {'sender_type': 'bot'}, {'chat_type': 'group'},
                        {'create_time': '0'}, {'message_id': '../bad'}, {'chat_id': ''}):
            self.assertIsNone(self.bridge.ingest(event(**changes)))
        self.assertIsNone(self.store.claim())

    def test_duplicate_delivery_publishes_exactly_one_real_record(self):
        job_id = self.bridge.ingest(event())
        self.assertIsNone(self.bridge.ingest(event()))
        self.planner.decide.side_effect = [self.action('site.request', {'method': 'POST', 'path': '/api/life-moments/', 'body': self.payload}),
                                          self.action('final')]
        self.bridge.work(self.store.claim())
        self.assertEqual(LifeMoment.query.count(), 1)
        self.assertIsNone(self.store.claim())
        with self.store.connect() as c:
            self.assertEqual(c.execute('SELECT state FROM jobs WHERE id=?', (job_id,)).fetchone()[0], 'done')
            result = json.loads(c.execute('SELECT result FROM actions').fetchone()[0])
            self.assertEqual(result['status'], 201)

    def test_revoke_admin_blocks_dispatch(self):
        site = Site(self.app, 1)
        Role.query.first().permissions = Permission.COMMENT
        db.session.commit()
        with self.assertRaises(ValueError):
            site.request({'method': 'POST', 'path': '/api/life-moments/', 'body': self.payload})
        self.assertEqual(LifeMoment.query.count(), 0)

    def test_duplicate_model_write_reuses_result(self):
        self.bridge.ingest(event())
        action = self.action('site.request', {'method': 'POST', 'path': '/api/life-moments/', 'body': self.payload})
        self.planner.decide.side_effect = [action, action, self.action('final')]
        self.bridge.work(self.store.claim())
        self.assertEqual(LifeMoment.query.count(), 1)
        with self.store.connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM actions').fetchone()[0], 1)

    def test_original_sender_verified_again_before_execution(self):
        self.lark.message.return_value['sender']['id'] = 'ou_other'
        self.bridge.ingest(event())
        self.bridge.work(self.store.claim())
        self.planner.decide.assert_not_called()

    def test_restart_keeps_pending_and_marks_running_uncertain(self):
        self.bridge.ingest(event())
        job = self.store.claim()
        self.store.action(job['id'], 'site.request', {'method': 'POST'})
        self.bridge.ingest(event('om_second'))
        self.store.recover()
        self.assertEqual(self.store.claim()['message_id'], 'om_second')
        with self.store.connect() as c:
            self.assertEqual(c.execute('SELECT state FROM jobs WHERE id=?', (job['id'],)).fetchone()[0], 'interrupted')

    def test_uncertain_write_not_retried_by_planner(self):
        self.bridge.ingest(event())
        self.planner.decide.return_value = self.action('site.request', {'method': 'POST', 'path': '/api/life-moments/'})
        with patch.object(self.bridge, 'execute', side_effect=RuntimeError('请求超时')) as execute:
            self.bridge.work(self.store.claim())
        execute.assert_called_once()
        self.assertEqual(self.planner.decide.call_count, 1)
        with self.store.connect() as c:
            self.assertEqual(c.execute('SELECT state FROM actions').fetchone()[0], 'running')

    def test_cancel_interrupts_queued_task_without_model(self):
        self.bridge.ingest(event())
        self.bridge.ingest(event('om_cancel', content='/cancel'))
        self.bridge.work(self.store.claim())
        self.planner.decide.assert_not_called()

    def test_new_session_has_no_previous_context(self):
        self.bridge.ingest(event())
        first = self.store.claim()
        self.store.finish(first, 'done', '之前的操作')
        self.bridge.ingest(event('om_reset', content='/new'))
        self.bridge.ingest(event('om_next'))
        new = self.store.claim()
        self.assertNotEqual(new['session'], first['session'])
        self.assertEqual(self.store.history(new), [])

    def test_outbox_failure_retries_same_dedup_key(self):
        self.bridge.ingest(event())
        row = self.store.pending_reply()
        self.store.reply_result(row, False)
        self.assertIsNone(self.store.pending_reply())
        with self.store.connect() as c:
            c.execute('UPDATE outbox SET next_attempt=0')
        retry = self.store.pending_reply()
        self.assertEqual(row['dedup'], retry['dedup'])
        self.store.reply_result(retry, True)
        self.assertIsNone(self.store.pending_reply())

    def test_site_rejects_external_paths_auth_and_files(self):
        site = Site(self.app, 1)
        for path in ('https://evil.test/api/life-moments/', '/api/../etc/passwd', '/api/get-file/',
                     '/api/github-login/code', '/api/life-moments/?page=1', '//evil/api/test', '/api/%2e%2e/private'):
            with self.assertRaises(Exception):
                site.request({'path': path})
        self.assertIn('/api/life-moments/', [r['path'] for r in site.catalog()])

    def test_site_crud_preserves_validation_and_auth(self):
        site = Site(self.app, 1)
        created = site.request({'method': 'POST', 'path': '/api/life-moments/', 'body': self.payload})
        self.assertEqual(created['status'], 201)
        path = '/api/life-moments/{}/'.format(created['data']['id'])
        self.assertEqual(site.request({'method': 'PUT', 'path': path, 'body': dict(self.payload, text='')})['status'], 400)
        self.assertEqual(site.request({'method': 'PUT', 'path': path, 'body': dict(self.payload, text='更新')})['status'], 200)
        self.assertEqual(site.request({'method': 'DELETE', 'path': path})['status'], 200)

    def test_server_error_stops_instead_of_suggesting_retry(self):
        site = Site(self.app, 1)
        fake = Mock(status_code=500)
        client = Mock()
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)
        client.open.return_value = fake
        with patch.object(self.app, 'test_client', return_value=client), self.assertRaises(RuntimeError):
            site.request({'method': 'POST', 'path': '/api/life-moments/', 'body': self.payload})

    def test_site_domain_and_prefix_are_configuration_only(self):
        with patch.dict(os.environ, {'LARK_BRIDGE_SITE_URL': ''}):
            self.assertEqual(public_links(), {'home': '/', 'moments': '/moments'})
        with patch.dict(os.environ, {'LARK_BRIDGE_SITE_URL': 'https://blog.example.test/project/'}):
            self.assertEqual(public_links()['moments'], 'https://blog.example.test/project/moments')
        for invalid in ('javascript:alert(1)', 'https://user:secret@example.test', 'https://example.test/?x=1',
                        'https://example.test/#section', 'https://example.test/a b'):
            with patch.dict(os.environ, {'LARK_BRIDGE_SITE_URL': invalid}), self.assertRaises(ValueError):
                public_links()

    def test_image_reference_is_scoped_to_conversation(self):
        media = Media(self.store, self.app)
        # Minimal header suffices for the storage signature check; external
        # provider output is separately validated as a complete PNG.
        asset = media.save('session-a', b'\x89PNG\r\n\x1a\n' + b'0' * 24)
        with self.assertRaises(ValueError):
            media.get('session-b', asset['asset_id'])
        self.assertEqual(media.get('session-a', asset['asset_id'])[1].parent, self.store.root / 'assets')

    def test_image_edit_sends_actual_source_multipart_and_keeps_original(self):
        media = Media(self.store, self.app)
        raw = b'\x89PNG\r\n\x1a\n' + b'0' * 24
        asset = media.save('s', raw)
        import base64
        response = Mock(status_code=200)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.iter_content.return_value = [json.dumps({'model': 'gpt-image-2.5-flare', 'data': [{'b64_json': base64.b64encode(raw).decode()}]}).encode()]
        with patch.dict(os.environ, {'CONTENT_CPA_API_KEY': 'test'}), patch('requests.Session') as factory, \
                patch('app.generation.providers.png_dimensions'):
            factory.return_value.post.return_value = response
            result = media.generate('s', '调亮', 'test-request', asset['asset_id'])
            args, kwargs = factory.return_value.post.call_args
            self.assertTrue(args[0].endswith('/v1/images/edits'))
            self.assertEqual(kwargs['files']['image'][1], raw)
            self.assertFalse(kwargs['allow_redirects'])
            self.assertEqual(result['operation'], 'edit')
        self.assertTrue(media.get('s', asset['asset_id'])[1].exists())

    def test_secrets_redacted_and_embedded_images_extracted(self):
        self.assertEqual(redact({'data': {'api_key': 'secret', 'id': 1}}), {'data': {'api_key': '[REDACTED]', 'id': 1}})
        self.assertEqual(image_keys({'content': [[{'tag': 'img', 'image_key': 'img_x'}]]}), ['img_x'])

    def test_lark_profile_is_pinned_and_credentials_do_not_cross_environment(self):
        lark = Lark('lark-cli', self.temp.name, 'cli_bound')
        with patch.dict(os.environ, {'DATABASE_URL': 'private-db', 'CONTENT_CPA_API_KEY': 'private-key'}), \
                patch('lark_bridge.lark.subprocess.run') as run:
            run.return_value = Mock(returncode=0, stdout='{}')
            lark.run(['auth', 'status'])
            args, kwargs = run.call_args
            self.assertEqual(args[0][:3], ['lark-cli', '--profile', 'cli_bound'])
            self.assertNotIn('DATABASE_URL', kwargs['env'])
            self.assertNotIn('CONTENT_CPA_API_KEY', kwargs['env'])

    def test_planner_has_no_database_or_image_credentials(self):
        process = Mock()
        process.poll.return_value = 0
        process.returncode = 1
        with patch.dict(os.environ, {'DATABASE_URL': 'private-db', 'CONTENT_CPA_API_KEY': 'private-key'}), \
                patch('lark_bridge.planner.subprocess.Popen', return_value=process) as popen:
            with self.assertRaises(RuntimeError):
                Planner('codex').decide({'message': '查询'}, [])
            args, kwargs = popen.call_args
            self.assertNotIn('DATABASE_URL', kwargs['env'])
            self.assertNotIn('CONTENT_CPA_API_KEY', kwargs['env'])
            self.assertIn('--ignore-user-config', args[0])
            self.assertIn('features.shell_tool=false', args[0])
            self.assertIn('--output-schema', args[0])

    def test_background_failure_stops_service_for_supervised_restart(self):
        with patch.object(self.bridge, 'worker', side_effect=RuntimeError('worker failed')), \
                patch.object(self.bridge, 'listen', side_effect=lambda *args: self.bridge.stop.wait(2)):
            with self.assertRaises(RuntimeError):
                self.bridge.run()
        self.assertTrue(self.bridge.stop.is_set())


if __name__ == '__main__':
    unittest.main()
