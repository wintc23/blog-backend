"""Image tool API, private storage and worker tests; isolated SQLite and fake provider."""
import io
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
import unittest
from PIL import Image
import test_guest_login as fixture
from app import db
from app.image_tool_models import ImageTool, ImageTask, ImageToolAsset, ImageToolItem, ImageToolSettings
from app.image_tools.worker import seed, run_one, recover, cleanup
from app.image_tools import provider, storage


class ImageToolsTests(unittest.TestCase):
    owner = fixture.GuestLoginTests.owner
    regular = fixture.GuestLoginTests.regular
    post = fixture.GuestLoginTests.post

    def setUp(self):
        fixture.GuestLoginTests.setUp(self)
        self.app.config.update(IMAGE_TOOLS_QINIU_UPLOAD_URL='https://up.test')
        self.cloud_data = {}
        for name, fn in {
            'put': lambda key, data, mime='image/png': self.cloud_data.__setitem__(key, data),
            'read': lambda key, limit=0: self.cloud_data[key],
            'delete': lambda key: self.cloud_data.pop(key, None),
            'signed_url': lambda key, **kwargs: 'https://s3.cn-south-1.qiniucs.com/private/' + key + '?signed=1',
            'upload_token': lambda *args: 'limited-token',
            'objects': lambda: [],
        }.items():
            mock = patch('app.image_tools.cloud.' + name, side_effect=fn)
            mock.start(); self.patches.append(mock)
        for model in (ImageTool, ImageTask, ImageToolAsset, ImageToolItem, ImageToolSettings):
            model.__table__.create(db.engine)
        from app.album_models import AlbumPhoto
        AlbumPhoto.__table__.create(db.engine)
        seed()
        self.headers = {'Authorization': self.regular.generate_auth_token(3600)}
        self.admin_headers = {'Authorization': self.owner.generate_auth_token(3600)}
        self.ready = patch('app.image_tools.provider.ready', return_value=True)
        self.ready.start()

    def tearDown(self):
        self.ready.stop()
        fixture.GuestLoginTests.tearDown(self)

    def png(self, color='blue'):
        out = io.BytesIO(); Image.new('RGB', (20, 10), color).save(out, 'PNG'); return out.getvalue()

    def create(self, slug='cartoon'):
        result = self.client.post('/api/image-tasks/', headers=self.headers, json={'tool_slug': slug})
        self.assertEqual(result.status_code, 201, result.get_json())
        return result.get_json()['task']['id']

    def cloud_upload(self, endpoint, headers, raw, name='image.png', mime='image/png'):
        grant = self.client.post(endpoint, headers=headers, json={'action': 'authorize', 'name': name, 'size': len(raw), 'mime': mime})
        if grant.status_code != 200:
            return grant
        data = grant.get_json()
        self.cloud_data[data['key']] = raw
        return self.client.post(endpoint, headers=headers, json={'action': 'complete', 'ticket': data['ticket']})

    def upload(self, task_id, name='image.png', headers=None):
        response = self.cloud_upload('/api/image-tasks/{}/assets/'.format(task_id), headers or self.headers, self.png(), name)
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()['id']

    def read(self, task_id):
        return self.client.get('/api/image-tasks/{}/'.format(task_id), headers=self.headers).get_json()['task']

    def submit(self, task_id):
        return self.client.post('/api/image-tasks/{}/submit/'.format(task_id), headers=self.headers, json={})

    def test_public_tools_hide_instructions_and_admin_only_edits(self):
        tools = self.client.get('/api/image-tools/').get_json()['tools']
        self.assertEqual(len(tools), 3)
        self.assertNotIn('instruction', tools[0]['config'])
        self.assertEqual(self.client.get('/api/image-tools/admin/templates/', headers=self.headers).status_code, 403)
        tool = self.client.get('/api/image-tools/admin/templates/', headers=self.admin_headers).get_json()['tools'][0]
        task_id = self.create(tool['slug'])
        tool['config']['name'] = '新名称'
        result = self.client.post('/api/image-tools/admin/templates/', headers=self.admin_headers, json=tool)
        self.assertEqual(result.status_code, 200, result.get_json())
        self.assertEqual(result.get_json()['tool']['version'], 2)
        self.assertNotEqual(self.read(task_id)['config']['name'], '新名称')
        self.assertEqual(self.client.post('/api/image-tools/admin/templates/', headers=self.admin_headers, json=tool).status_code, 409)

    def test_missing_tables_return_readable_service_error(self):
        ImageTool.__table__.drop(db.engine)
        response = self.client.get('/api/image-tools/', headers={'Origin': 'http://localhost:8000'})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()['message'], '图片服务暂不可用，请稍后重试。')
        self.assertEqual(response.headers['Cache-Control'], 'no-store')

    def test_owner_isolation_and_upload_validation(self):
        task_id = self.create()
        self.assertEqual(self.client.get('/api/image-tasks/{}/'.format(task_id)).status_code, 401)
        self.assertEqual(self.client.get('/api/image-tasks/{}/'.format(task_id), headers=self.admin_headers).status_code, 404)
        self.assertEqual(self.client.post('/api/image-tasks/{}/assets/'.format(task_id), headers=self.headers, data={'image': (io.BytesIO(b'<svg/>'), 'bad.svg')}).status_code, 400)
        asset_id = self.upload(task_id, '../../secret.png')
        asset = ImageToolAsset.query.get(asset_id)
        self.assertEqual(asset.name, 'secret.png')
        self.assertEqual(self.client.get('/api/image-assets/{}/'.format(asset_id)).status_code, 403)
        self.assertEqual(self.client.get('/api' + self.read(task_id)['inputs'][0]['url'], buffered=True).status_code, 302)

    def test_handoff_can_only_upload_current_draft_and_revokes(self):
        task_id = self.create()
        result = self.client.post('/api/image-tasks/{}/handoff/'.format(task_id), headers=self.headers, json={}).get_json()
        headers = {'X-Image-Upload-Token': result['token']}
        self.assertEqual(self.client.get('/api/image-upload-session/', headers=headers).status_code, 200)
        self.assertEqual(self.client.post('/api/image-tasks/{}/submit/'.format(task_id), headers=headers, json={}).status_code, 401)
        response = self.cloud_upload('/api/image-upload-session/', headers, self.png(), 'phone.png')
        self.assertEqual(response.status_code, 201, response.get_json())
        self.assertEqual(len(self.read(task_id)['inputs']), 1)
        self.assertEqual(self.submit(task_id).status_code, 200)
        self.assertEqual(self.client.get('/api/image-upload-session/', headers=headers).status_code, 410)
        self.assertEqual(self.client.post('/api/image-upload-session/', headers=headers, data={'image': (io.BytesIO(self.png()), 'late.png')}).status_code, 410)

    def test_batch_order_idempotence_worker_download_and_private_share(self):
        task_id = self.create('restore')
        first, second = self.upload(task_id, 'one.png'), self.upload(task_id, 'two.png')
        response = self.client.patch('/api/image-tasks/{}/'.format(task_id), headers=self.headers, json={'options': {'ratio': '2:3'}, 'asset_order': [second, first]})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.submit(task_id).status_code, 200)
        self.assertEqual(self.submit(task_id).status_code, 200)
        self.assertEqual(ImageToolItem.query.count(), 2)
        with patch('app.image_tools.provider.generate', return_value=self.png('red')) as generate:
            self.assertTrue(run_one()); self.assertTrue(run_one()); self.assertFalse(run_one())
            self.assertEqual(generate.call_args_list[0].args[1], '2:3')
            self.assertEqual(generate.call_args_list[0].args[2].id, second)
        task = self.read(task_id)
        self.assertEqual(task['status'], 'completed')
        self.assertEqual(len(task['outputs']), 2)
        result = self.client.get('/api/image-tasks/{}/download/'.format(task_id), headers=self.headers)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(len(result.get_json()['files']), 2)
        self.assertTrue(all(f['url'].startswith('https://s3.') for f in result.get_json()['files']))
        share = self.client.post('/api/image-tasks/{}/share/'.format(task_id), headers=self.headers, json={'asset_ids': [task['outputs'][0]['id']]}).get_json()
        public = self.client.get('/api/image-shares/{}/'.format(share['token'])).get_json()
        self.assertEqual(len(public['outputs']), 1)
        self.assertNotIn('inputs', public); self.assertNotIn('options', public)
        signed = '/api' + public['outputs'][0]['url']
        self.assertEqual(self.client.get(signed, buffered=True).status_code, 302)
        self.client.delete('/api/image-tasks/{}/share/'.format(task_id), headers=self.headers)
        self.assertEqual(self.client.get(signed, buffered=True).status_code, 403)

    def test_retry_preserves_outputs_and_quota_is_per_image(self):
        task_id = self.create(); self.upload(task_id)
        self.submit(task_id)
        with patch('app.image_tools.provider.generate', return_value=self.png()): run_one()
        item = self.read(task_id)['items'][0]
        result = self.client.post('/api/image-tasks/{}/items/{}/retry/'.format(task_id, item['id']), headers=self.headers, json={})
        self.assertEqual(result.status_code, 200, result.get_json())
        self.assertEqual(len(result.get_json()['task']['outputs']), 1)
        with patch('app.image_tools.provider.generate', return_value=self.png()): run_one()
        self.assertEqual(len(self.read(task_id)['outputs']), 2)
        limits = ImageToolSettings.query.get(1); limits.user_per_hour = 2; db.session.commit()
        self.assertEqual(self.client.post('/api/image-tasks/{}/items/{}/retry/'.format(task_id, item['id']), headers=self.headers, json={}).status_code, 429)

    def test_timeout_and_expired_lease_never_automatically_retry(self):
        task_id = self.create(); self.upload(task_id); self.submit(task_id)
        with patch('app.image_tools.provider.generate', side_effect=TimeoutError): self.assertTrue(run_one())
        self.assertFalse(run_one())
        self.assertEqual(self.read(task_id)['items'][0]['status'], 'uncertain')
        item = ImageToolItem.query.first(); item.status = 'running'; item.lease_until = datetime.utcnow() - timedelta(seconds=1); db.session.commit()
        recover(); self.assertEqual(ImageToolItem.query.first().status, 'uncertain')

    def test_advanced_options_preserve_one_output_per_source(self):
        task_id = self.create('cartoon')
        response = self.client.patch('/api/image-tasks/{}/'.format(task_id), headers=self.headers,
            json={'options': {'ratio': '2:3', 'count': 4, 'fields': {'style': '复古漫画'}}})
        self.assertEqual(response.status_code, 200)
        options = response.get_json()['task']['options']
        self.assertEqual(options['ratio'], '2:3')
        self.assertEqual(options['count'], 1)
        self.assertEqual(options['fields']['style'], '复古漫画')

    def test_advanced_options_reject_values_outside_template(self):
        task_id = self.create('cartoon')
        for options in [{'ratio': '99:1'}, {'fields': {'style': 'unknown'}}, {'fields': {'unknown': 'value'}}]:
            result = self.client.patch('/api/image-tasks/{}/'.format(task_id), headers=self.headers, json={'options': options})
            self.assertEqual(result.status_code, 400)
        self.assertEqual(self.client.patch('/api/image-tasks/{}/'.format(task_id), headers=self.headers,
            json={'options': {'prompt': '保留衣服上的图案'}}).status_code, 200)
        self.assertEqual(self.read(task_id)['options']['count'], 1)

    def test_free_prompt_and_required_inputs_validated(self):
        tool = ImageTool.query.get('create')
        config = json.loads(tool.config_json); config['default_count'] = 3
        tool.config_json = json.dumps(config); db.session.commit()
        task_id = self.create('create')
        self.assertEqual(self.submit(task_id).status_code, 400)
        self.client.patch('/api/image-tasks/{}/'.format(task_id), headers=self.headers, json={'options': {'prompt': 'a quiet forest', 'count': 3}})
        self.assertEqual(self.submit(task_id).status_code, 200)
        self.assertEqual(ImageToolItem.query.count(), 3)
        task_id = self.create('cartoon')
        self.assertEqual(self.submit(task_id).status_code, 400)

    def test_delete_and_cleanup_revokes_asset_urls_without_touching_other_tasks(self):
        first, second = self.create(), self.create()
        self.upload(first); second_asset = self.upload(second)
        url = self.read(first)['inputs'][0]['url']
        self.client.delete('/api/image-tasks/{}/'.format(first), headers=self.headers)
        self.assertEqual(self.client.get('/api' + url).status_code, 404)
        cleanup()
        self.assertIsNone(ImageTask.query.get(first))
        self.assertIn(storage.key(ImageToolAsset.query.get(second_asset)), self.cloud_data)

    def test_deleting_and_cleaning_task_cannot_restore_hourly_quota(self):
        task_id = self.create(); self.upload(task_id); self.submit(task_id)
        self.client.delete('/api/image-tasks/{}/'.format(task_id), headers=self.headers)
        cleanup()
        self.assertIsNotNone(ImageTask.query.get(task_id))
        draft = self.create()
        self.assertEqual(self.read(draft)['quota']['remaining'], 9)
        old = datetime.utcnow() - timedelta(hours=2)
        ImageToolItem.query.filter_by(task_id=task_id).update(dict(created_at=old, started_at=old))
        db.session.commit()
        cleanup()
        self.assertIsNone(ImageTask.query.get(task_id))
        self.assertEqual(self.read(draft)['quota']['remaining'], 10)

    def test_clone_is_private_independent_draft(self):
        task_id = self.create(); self.upload(task_id)
        result = self.client.post('/api/image-tasks/{}/clone/'.format(task_id), headers=self.headers, json={})
        self.assertEqual(result.status_code, 201, result.get_json())
        clone = result.get_json()['task']
        self.assertNotEqual(clone['inputs'][0]['id'], self.read(task_id)['inputs'][0]['id'])
        self.assertEqual(clone['status'], 'draft')

    def test_model_is_always_image_25_even_if_other_environment_models_are_set(self):
        self.assertEqual(provider.IMAGE_MODEL, 'gpt-image-2.5-flare')
        with patch.dict(os.environ, {'IMAGE_TOOLS_MODEL': 'other', 'LARK_BRIDGE_IMAGE_MODEL': 'other'}), patch('requests.Session') as client:
            response = client.return_value.__enter__.return_value.post.return_value.__enter__.return_value
            response.status_code = 200
            import base64
            response.iter_content.return_value = [json.dumps({'data': [{'b64_json': base64.b64encode(self.png()).decode()}]}).encode()]
            with patch.dict(os.environ, {'CONTENT_CPA_API_KEY': 'test-only'}):
                provider.generate('paint', '1:1', None, 'test-id')
            self.assertEqual(client.return_value.__enter__.return_value.post.call_args.kwargs['json']['model'], 'gpt-image-2.5-flare')

    def test_upload_session_rotation_and_expiry(self):
        task_id = self.create()
        url = '/api/image-tasks/{}/handoff/'.format(task_id)
        first = self.client.post(url, headers=self.headers, json={}).get_json()['token']
        second = self.client.post(url, headers=self.headers, json={}).get_json()['token']
        self.assertNotEqual(first, second)
        self.assertEqual(self.client.get('/api/image-upload-session/', headers={'X-Image-Upload-Token': first}).status_code, 410)
        with patch('itsdangerous.TimestampSigner.get_timestamp', return_value=0):
            from app.api.image_tools import signer
            task = ImageTask.query.get(task_id)
            expired = signer().dumps(dict(task=task_id, nonce=task.upload_nonce, scope='upload'))
        self.assertEqual(self.client.get('/api/image-upload-session/', headers={'X-Image-Upload-Token': expired}).status_code, 410)
        self.client.delete(url, headers=self.headers)
        self.assertEqual(self.client.get('/api/image-upload-session/', headers={'X-Image-Upload-Token': second}).status_code, 410)

    def test_malformed_order_does_not_overwrite_existing_draft(self):
        task_id = self.create(); asset_id = self.upload(task_id)
        for ids in [[{}], [asset_id, asset_id], ['not-owned']]:
            response = self.client.patch('/api/image-tasks/{}/'.format(task_id), headers=self.headers,
                json={'options': {'prompt': 'should rollback'}, 'asset_order': ids})
            self.assertEqual(response.status_code, 409, response.get_json())
        self.assertEqual(self.read(task_id)['options'], {})

    def test_original_files_survive_and_private_copy_drops_metadata(self):
        task_id = self.create()
        data = io.BytesIO()
        Image.new('RGB', (20, 10), 'red').save(data, 'JPEG', comment=b'private location')
        raw = data.getvalue()
        response = self.cloud_upload('/api/image-tasks/{}/assets/'.format(task_id), self.headers, raw, 'old.jpg', 'image/jpeg')
        self.assertEqual(response.status_code, 201)
        asset = ImageToolAsset.query.get(response.get_json()['id'])
        self.assertEqual(storage.read(asset, True), raw)
        with Image.open(io.BytesIO(storage.read(asset))) as image:
            self.assertEqual(image.format, 'PNG')
            self.assertNotIn('exif', image.info)

    def test_disabled_tool_cannot_submit_or_execute_queued_job(self):
        task_id = self.create(); self.upload(task_id)
        tool = ImageTool.query.get('cartoon'); tool.enabled = False; db.session.commit()
        self.assertEqual(self.submit(task_id).status_code, 503)
        tool = ImageTool.query.get('cartoon'); tool.enabled = True; db.session.commit(); self.submit(task_id)
        tool = ImageTool.query.get('cartoon'); tool.enabled = False; db.session.commit()
        with patch('app.image_tools.provider.generate') as generate:
            run_one(); generate.assert_not_called()
        self.assertEqual(self.read(task_id)['items'][0]['status'], 'cancelled')

    def guest_headers(self):
        response = self.client.post('/api/guest-login/', json={})
        self.assertEqual(response.status_code, 200, response.get_json())
        return {'Authorization': response.get_json()['token']}

    def test_guest_can_upload_submit_and_cannot_read_other_guest_task(self):
        self.headers = self.guest_headers()
        task = self.create()
        self.upload(task)
        self.assertEqual(self.submit(task).status_code, 200)
        other = self.guest_headers()
        response = self.client.get('/api/image-tasks/{}/'.format(task), headers=other)
        self.assertIn(response.status_code, (403, 404))

    def test_guest_ip_limit_counts_images_across_accounts_and_keeps_rejected_draft(self):
        limits = ImageToolSettings.query.get(1); limits.user_per_hour = 2; db.session.commit()
        self.headers = self.guest_headers()
        first = self.create(); self.upload(first); self.upload(first)
        self.assertEqual(self.submit(first).status_code, 200)
        # Idempotent submit must not charge the shared counter a second time.
        self.assertEqual(self.submit(first).status_code, 200)
        self.headers = self.guest_headers()
        second = self.create(); self.upload(second)
        response = self.submit(second)
        self.assertEqual(response.status_code, 429, response.get_json())
        self.assertIn('Retry-After', response.headers)
        self.assertEqual(self.read(second)['status'], 'draft')
        self.assertEqual(ImageToolItem.query.filter_by(task_id=second).count(), 0)
        # Raising the existing admin setting changes the guest network cap too.
        limits = ImageToolSettings.query.get(1); limits.user_per_hour = 3; db.session.commit()
        self.assertEqual(self.submit(second).status_code, 200)

    def test_guest_retry_obeys_shared_ip_limit_but_regular_account_is_unaffected(self):
        limits = ImageToolSettings.query.get(1); limits.user_per_hour = 2; db.session.commit()
        self.headers = self.guest_headers()
        first = self.create(); self.upload(first); self.submit(first)
        item = ImageToolItem.query.filter_by(task_id=first).one(); item.status = 'failed'; db.session.commit()
        item_id = item.id
        guest_a = self.headers
        self.headers = self.guest_headers()
        second = self.create(); self.upload(second); self.submit(second)
        response = self.client.post('/api/image-tasks/{}/items/{}/retry/'.format(first, item_id), headers=guest_a, json={})
        self.assertEqual(response.status_code, 429)
        self.headers = {'Authorization': self.regular.generate_auth_token(3600)}
        regular = self.create(); self.upload(regular)
        self.assertEqual(self.submit(regular).status_code, 200)

    def test_migration_creates_the_expected_tables_and_can_be_reverted(self):
        import importlib.util
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        from sqlalchemy import create_engine, inspect, text
        engine = create_engine('sqlite://')
        with engine.connect() as connection:
            connection.execute(text('CREATE TABLE users (id INTEGER PRIMARY KEY)'))
            spec = importlib.util.spec_from_file_location('image_tools_migration', Path(__file__).parents[1] / 'migrations/versions/20260918_image_tools.py')
            module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
            with Operations.context(MigrationContext.configure(connection)):
                module.upgrade()
                for name in ['image_tools', 'image_tool_tasks', 'image_tool_assets', 'image_tool_items']:
                    self.assertIn(name, inspect(connection).get_table_names())
                module.downgrade()
            self.assertEqual(inspect(connection).get_table_names(), ['users'])
        engine.dispose()

    def test_default_limits_and_admin_updates(self):
        response = self.client.get('/api/image-tools/admin/limits/', headers=self.admin_headers)
        limits = response.get_json()['limits']
        self.assertEqual(limits['global_per_minute'], 10)
        self.assertEqual(limits['user_per_hour'], 10)
        self.assertEqual(self.client.put('/api/image-tools/admin/limits/', headers=self.headers, json=limits).status_code, 403)
        limits['global_per_minute'] = 2
        limits['user_per_hour'] = 3
        response = self.client.put('/api/image-tools/admin/limits/', headers=self.admin_headers, json=limits)
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()['limits']['global_per_minute'], 2)
        self.assertEqual(self.client.put('/api/image-tools/admin/limits/', headers=self.admin_headers, json=limits).status_code, 409)

    def test_account_hourly_limit_counts_batch_and_retry(self):
        task_id = self.create()
        for index in range(10): self.upload(task_id, str(index) + '.png')
        self.assertEqual(self.submit(task_id).status_code, 200)
        another = self.create(); self.upload(another)
        response = self.submit(another)
        self.assertEqual(response.status_code, 429, response.get_json())
        self.assertGreater(int(response.headers['Retry-After']), 0)
        self.assertEqual(self.read(another)['quota']['remaining'], 0)
        ImageToolItem.query.update(dict(created_at=datetime.utcnow() - timedelta(hours=2)))
        db.session.commit()
        self.assertEqual(self.submit(another).status_code, 200)

    def test_global_dispatch_rate_queues_work_until_window_expires(self):
        limits = ImageToolSettings.query.get(1); limits.global_per_minute = 1; db.session.commit()
        task_id = self.create(); self.upload(task_id); self.upload(task_id); self.submit(task_id)
        with patch('app.image_tools.provider.generate', return_value=self.png()) as generate:
            self.assertTrue(run_one())
            self.assertFalse(run_one())
            self.assertEqual(generate.call_count, 1)
            ImageToolItem.query.filter_by(status='completed').update(dict(started_at=datetime.utcnow() - timedelta(seconds=61)))
            db.session.commit()
            self.assertTrue(run_one())
            self.assertEqual(generate.call_count, 2)

    def test_admin_bypasses_both_generation_limits(self):
        limits = ImageToolSettings.query.get(1); limits.global_per_minute = 1; limits.user_per_hour = 1; db.session.commit()
        self.headers = self.admin_headers
        task_id = self.create(); self.upload(task_id); self.upload(task_id)
        self.assertEqual(self.submit(task_id).status_code, 200)
        self.assertTrue(self.read(task_id)['quota']['exempt'])
        with patch('app.image_tools.provider.generate', return_value=self.png()) as generate:
            self.assertTrue(run_one()); self.assertTrue(run_one())
            self.assertEqual(generate.call_count, 2)
        self.assertTrue(all(i.quota_exempt for i in ImageToolItem.query.all()))

    def test_hourly_dispatch_guard_includes_older_queued_reservations(self):
        task_id = self.create(); self.upload(task_id); self.upload(task_id); self.submit(task_id)
        limits = ImageToolSettings.query.get(1); limits.user_per_hour = 1; db.session.commit()
        with patch('app.image_tools.provider.generate', return_value=self.png()) as generate:
            self.assertTrue(run_one()); self.assertFalse(run_one())
            self.assertEqual(generate.call_count, 1)

    def test_parallel_workers_cannot_exceed_global_minute_limit(self):
        from concurrent.futures import ThreadPoolExecutor
        limits = ImageToolSettings.query.get(1); limits.global_per_minute = 1; db.session.commit()
        task_id = self.create(); self.upload(task_id); self.upload(task_id); self.submit(task_id)
        def run():
            with self.app.app_context():
                try: return run_one()
                finally: db.session.remove()
        with patch('app.image_tools.provider.generate', return_value=self.png()) as generate:
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: run(), range(2)))
            self.assertEqual(sum(results), 1)
            self.assertEqual(generate.call_count, 1)

    def test_parallel_account_submissions_share_hourly_quota(self):
        from concurrent.futures import ThreadPoolExecutor
        limits = ImageToolSettings.query.get(1); limits.user_per_hour = 1; db.session.commit()
        first, second = self.create(), self.create(); self.upload(first); self.upload(second)
        headers = dict(self.headers)
        def submit(task_id):
            with self.app.test_client() as client:
                return client.post('/api/image-tasks/{}/submit/'.format(task_id), headers=headers, json={}).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(submit, [first, second]))
        self.assertEqual(sorted(statuses), [200, 429])



    def test_drafts_are_not_jobs_until_first_successful_submit(self):
        task = self.create()
        self.upload(task)
        self.assertEqual(self.client.get('/api/image-tasks/', headers=self.headers).get_json()['tasks'], [])
        self.assertEqual(self.client.get('/api/image-tools/admin/jobs/', headers=self.admin_headers).get_json()['jobs'], [])
        self.submit(task)
        rows = self.client.get('/api/image-tasks/', headers=self.headers).get_json()['tasks']
        self.assertEqual([r['id'] for r in rows], [task])

    def test_direct_upload_tickets_are_scoped_idempotent_and_validate_content(self):
        task, other = self.create(), self.create()
        endpoint = '/api/image-tasks/{}/assets/'.format(task)
        grant = self.client.post(endpoint, headers=self.headers, json={'action':'authorize','name':'safe.png','size':1000,'mime':'image/png'}).get_json()
        self.cloud_data[grant['key']] = self.png()
        data = {'action':'complete','ticket':grant['ticket']}
        self.assertEqual(self.client.post('/api/image-tasks/{}/assets/'.format(other), headers=self.headers, json=data).status_code, 403)
        first = self.client.post(endpoint, headers=self.headers, json=data)
        second = self.client.post(endpoint, headers=self.headers, json=data)
        self.assertEqual(first.get_json()['id'], second.get_json()['id'])
        self.assertEqual(len(self.read(task)['inputs']), 1)
        grant = self.client.post(endpoint, headers=self.headers, json={'action':'authorize','name':'bad.png','size':1000,'mime':'image/png'}).get_json()
        self.cloud_data[grant['key']] = b'not an image'
        self.assertEqual(self.client.post(endpoint, headers=self.headers, json={'action':'complete','ticket':grant['ticket']}).status_code, 400)
        self.assertEqual(len(self.read(task)['inputs']), 1)
        self.assertFalse((Path(self.app.instance_path) / 'image-tools' / (first.get_json()['id'] + '.png')).exists())

    def test_backend_analytics_are_idempotent_and_exclude_private_content(self):
        from app.models import StatEvent
        task = self.create(); self.upload(task, 'private-name.png')
        self.client.patch('/api/image-tasks/{}/'.format(task), headers=self.headers, json={'options':{'prompt':'private prompt'}})
        self.submit(task); self.submit(task)
        with patch('app.image_tools.provider.generate', return_value=self.png()): run_one()
        events = StatEvent.query.filter(StatEvent.name.like('image_tool.%')).all()
        self.assertEqual([e.name for e in events].count('image_tool.submitted'), 1)
        self.assertEqual([e.name for e in events].count('image_tool.result'), 1)
        self.assertTrue(any(e.name == 'image_tool.upload_complete' for e in events))
        for event in events:
            self.assertNotIn('private-name', event.params)
            self.assertNotIn('private prompt', event.params)
            self.assertNotIn('ticket', event.params)

    def test_upload_policy_is_public_but_only_admin_can_change_it(self):
        public = self.client.get('/api/image-tools/upload-policy/').get_json()
        self.assertEqual(public['max_bytes'], 20 * 1024 * 1024)
        self.assertEqual(public['processing_max_edge'], 2048)
        values = self.client.get('/api/image-tools/admin/limits/', headers=self.admin_headers).get_json()['limits']
        values.update(upload_max_mb=50, upload_max_megapixels=60, processing_max_edge=1024)
        self.assertEqual(self.client.put('/api/image-tools/admin/limits/', headers=self.headers, json=values).status_code, 403)
        saved = self.client.put('/api/image-tools/admin/limits/', headers=self.admin_headers, json=values)
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(self.client.get('/api/image-tools/upload-policy/').get_json()['max_bytes'], 50 * 1024 * 1024)
        self.assertEqual(self.client.put('/api/image-tools/admin/limits/', headers=self.admin_headers, json=values).status_code, 409)
        values = saved.get_json()['limits']; values['processing_max_edge'] = 50000
        self.assertEqual(self.client.put('/api/image-tools/admin/limits/', headers=self.admin_headers, json=values).status_code, 400)
        self.assertEqual(self.client.get('/api/image-tools/upload-policy/').get_json()['processing_max_edge'], 1024)

    def test_original_is_retained_while_only_model_input_is_resized(self):
        task = self.create()
        data = io.BytesIO(); Image.new('RGBA', (1800, 900), (10, 50, 100, 128)).save(data, 'PNG')
        raw = data.getvalue()
        limits = ImageToolSettings.query.get(1); limits.processing_max_edge = 512; db.session.commit()
        response = self.cloud_upload('/api/image-tasks/{}/assets/'.format(task), self.headers, raw)
        self.assertEqual(response.status_code, 201)
        asset = ImageToolAsset.query.get(response.get_json()['id'])
        self.assertEqual(storage.read(asset, True), raw)
        with Image.open(io.BytesIO(storage.read(asset))) as resized:
            self.assertEqual(resized.size, (512, 256))
            self.assertEqual(resized.getpixel((20,20))[3], 128)
        output = storage.store(task, raw, 'result.png', kind='output')
        self.assertEqual((output.width, output.height), (1800, 900))

    def test_upload_ticket_keeps_policy_when_admin_changes_limits_mid_upload(self):
        task = self.create(); endpoint = '/api/image-tasks/{}/assets/'.format(task)
        data = io.BytesIO(); Image.new('RGB', (1500, 1000)).save(data, 'PNG'); raw = data.getvalue()
        grant = self.client.post(endpoint, headers=self.headers, json={'action':'authorize','name':'image.png','size':len(raw),'mime':'image/png'}).get_json()
        limits=ImageToolSettings.query.get(1); limits.upload_max_megapixels=1; db.session.commit()
        self.cloud_data[grant['key']] = raw
        result=self.client.post(endpoint, headers=self.headers, json={'action':'complete','ticket':grant['ticket']})
        self.assertEqual(result.status_code, 201)
        # New credentials use the new policy and cannot bypass its decoded pixel limit.
        result=self.cloud_upload(endpoint, self.headers, raw)
        self.assertEqual(result.status_code, 400)


if __name__ == '__main__': unittest.main()
