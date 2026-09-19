"""Album ACL, durable cloud references and single-use approved device login."""
from datetime import datetime, timedelta
import importlib.util
from pathlib import Path
from unittest.mock import patch
import unittest
from alembic.migration import MigrationContext
from alembic.operations import Operations
import test_image_tools as fixture
from app import db
from app.models import LifeMoment, User
from app.album_models import Album, AlbumPhoto, AlbumItem, DeviceLogin
from app.image_tool_models import ImageTask, ImageToolAsset
from app.image_tools import storage
from app.image_tools.worker import cleanup


class AlbumsTests(unittest.TestCase):
    owner = fixture.ImageToolsTests.owner
    regular = fixture.ImageToolsTests.regular
    png = fixture.ImageToolsTests.png
    cloud_upload = fixture.ImageToolsTests.cloud_upload
    create = fixture.ImageToolsTests.create
    upload = fixture.ImageToolsTests.upload

    def setUp(self):
        fixture.ImageToolsTests.setUp(self)
        # Keep two independent administrators to exercise owner isolation.
        self.regular.role = self.owner.role
        visitor = User(id_string='qq-album-viewer', username='viewer')
        db.session.add(visitor); db.session.commit()
        self.viewer_headers = {'Authorization': visitor.generate_auth_token(3600)}
        for model in (Album, AlbumItem, DeviceLogin, LifeMoment): model.__table__.create(db.engine)

    def tearDown(self):
        fixture.ImageToolsTests.tearDown(self)

    def album(self, visibility='private'):
        response = self.client.post('/api/albums/', headers=self.headers, json={'title': '相册', 'visibility': visibility})
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()

    def photo(self):
        response = self.cloud_upload('/api/album-uploads/', self.headers, self.png())
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()

    def add(self, album, source):
        response = self.client.post('/api/albums/{}/photos/'.format(album['id']), headers=self.headers, json={'sources': [source]})
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def test_visibility_source_acl_multi_album_and_revocation(self):
        private, public = self.album(), self.album('public')
        photo = self.photo(); source = {'type': 'photo', 'id': photo['id']}
        private = self.add(private, source); public = self.add(public, source)
        self.add(private, source)
        self.assertEqual(AlbumPhoto.query.count(), 1)
        self.assertEqual(AlbumItem.query.count(), 2)
        self.assertEqual(self.client.get('/api/albums/{}/'.format(private['id'])).status_code, 404)
        self.assertEqual(self.client.get('/api/albums/{}/'.format(private['id']), headers=self.admin_headers).status_code, 404)
        response = self.client.post('/api/albums/{}/photos/'.format(private['id']), headers=self.admin_headers, json={'sources': [source]})
        self.assertEqual(response.status_code, 404)
        public_view = self.client.get('/api/albums/{}/'.format(public['id'])).get_json()
        image = '/api' + public_view['photos'][0]['url']
        self.assertEqual(self.client.get(image).status_code, 302)
        self.assertIn('qiniucs.com', self.client.get(image).location)
        self.assertNotIn('cloud_key', public_view['photos'][0])
        patch_result = self.client.patch('/api/albums/{}/'.format(public['id']), headers=self.headers, json={'version': public['version'], 'title': '私密', 'visibility': 'private'})
        self.assertEqual(patch_result.status_code, 200, patch_result.get_json())
        self.assertEqual(self.client.get(image).status_code, 403)
        self.client.delete('/api/albums/{}/'.format(private['id']), headers=self.headers)
        self.assertEqual(AlbumPhoto.query.count(), 1)
        self.assertEqual(AlbumItem.query.count(), 1)
        self.assertEqual(len(self.cloud_data), 2)
        sources = self.client.get('/api/album-sources/', headers=self.headers)
        self.assertEqual(sources.status_code, 200, sources.get_json())
        self.assertEqual(sources.get_json()['list'][0]['source'], source)

    def test_only_admin_can_manage_albums_and_collect_photos(self):
        album = self.album('public'); path = '/api/albums/{}/'.format(album['id'])
        for headers, status in ((self.viewer_headers, 403), ({}, 401)):
            for method, endpoint, data in (
                ('post', '/api/albums/', {'title': '拒绝创建'}),
                ('patch', path, {'version': album['version'], 'title': '拒绝编辑'}),
                ('delete', path, None),
                ('post', path + 'photos/', {'sources': [{'type': 'photo', 'id': 'other'}]}),
                ('delete', path + 'photos/other/', None),
                ('post', '/api/album-uploads/', {'action': 'authorize', 'size': 100, 'mime': 'image/png'}),
                ('post', '/api/album-uploads/', {'action': 'complete', 'ticket': 'invalid'}),
                ('get', '/api/album-sources/', None),
                ('get', '/api/albums/?scope=mine', None),
            ):
                response = getattr(self.client, method)(endpoint, headers=headers, **({'json': data} if data else {}))
                self.assertEqual(response.status_code, status, (method, endpoint, response.get_json()))
            self.assertEqual(self.client.get('/api/albums/', headers=headers).status_code, 200)
            view = self.client.get(path, headers=headers).get_json()
            self.assertFalse(view['editable'])
        # An account that loses its administrator role cannot edit its former album.
        author = User.query.get(self.regular_id); author.role = User.verify_auth_token(self.viewer_headers['Authorization']).role
        db.session.commit()
        self.assertEqual(self.client.patch(path, headers=self.headers, json={'version': album['version']}).status_code, 403)
        self.assertFalse(self.client.get(path, headers=self.headers).get_json()['editable'])

    def test_generated_picture_survives_task_cleanup(self):
        task_id = self.create(); asset_id = self.upload(task_id)
        asset = ImageToolAsset.query.get(asset_id); key = storage.key(asset)
        album = self.add(self.album(), {'type': 'generated', 'id': asset_id})
        task = ImageTask.query.get(task_id); task.deleted_at = datetime.utcnow(); db.session.commit()
        cleanup()
        self.assertIsNone(ImageTask.query.get(task_id))
        self.assertIsNone(ImageToolAsset.query.get(asset_id))
        self.assertIn(key, self.cloud_data)
        self.assertEqual(self.client.get('/api' + album['photos'][0]['url']).status_code, 302)

    def test_foreign_source_rejected_sorting_version_and_removed_photo(self):
        task = self.create(); asset_id = self.upload(task)
        other = self.client.post('/api/albums/', headers=self.admin_headers, json={'title': '别人'}).get_json()
        result = self.client.post('/api/albums/{}/photos/'.format(other['id']), headers=self.admin_headers, json={'sources': [{'type': 'generated', 'id': asset_id}]})
        self.assertEqual(result.status_code, 403)
        album = self.album(); first, second = self.photo(), self.photo()
        album = self.add(album, {'type': 'photo', 'id': first['id']}); album = self.add(album, {'type': 'photo', 'id': second['id']})
        path = '/api/albums/{}/'.format(album['id'])
        result = self.client.patch(path, headers=self.headers, json={'version': album['version'], 'order': [second['id'], first['id']], 'cover_id': second['id']})
        self.assertEqual(result.status_code, 200, result.get_json())
        self.assertEqual(result.get_json()['photos'][0]['id'], second['id'])
        self.assertEqual(result.get_json()['cover']['id'], second['id'])
        self.assertEqual(self.client.patch(path, headers=self.headers, json={'version': album['version'], 'order': []}).status_code, 409)
        old_url = '/api' + result.get_json()['photos'][0]['url']
        self.client.delete(path + 'photos/{}/'.format(second['id']), headers=self.headers)
        self.assertEqual(self.client.get(old_url).status_code, 404)

    def test_moment_picture_import(self):
        moment = LifeMoment(date=datetime.utcnow().date(), text='动态', category='life', image_url='https://example.test/photo.jpg')
        db.session.add(moment); db.session.commit(); moment_id = moment.id
        sources = self.client.get('/api/album-sources/?source=moment', headers=self.headers)
        self.assertEqual(sources.status_code, 200, sources.get_json())
        self.assertEqual(sources.get_json()['list'][0]['source']['id'], moment_id)
        album = self.add(self.album(), {'type': 'moment', 'id': moment_id, 'index': 0})
        self.assertEqual(album['count'], 1)
        self.assertEqual(AlbumPhoto.query.one().url, 'https://example.test/photo.jpg')

    def test_published_media_stays_referenced_after_moment_deletion(self):
        from app.media_models import MediaAsset, MediaReference
        from app.media import cleanup_images
        from uuid import uuid4
        asset_id = uuid4().hex
        url = 'https://example.test/managed-images/{}.jpg'.format(asset_id)
        asset = MediaAsset(id=asset_id, owner_id=self.owner_id, storage_key='managed-images/{}.jpg'.format(asset_id), url=url,
                           mime_type='image/jpeg', byte_size=100, width=20, height=10, status='ready')
        db.session.add(asset); db.session.commit()
        moment = LifeMoment(date=datetime.utcnow().date(), text='照片', category='daily', image_url=url)
        db.session.add(moment); db.session.commit(); moment_id = moment.id
        self.add(self.album(), {'type': 'moment', 'id': moment_id, 'index': 0})
        db.session.delete(LifeMoment.query.get(moment_id)); db.session.commit()
        self.assertEqual(MediaReference.query.filter_by(asset_id=asset_id, content_type='album_photos').count(), 1)
        with patch('app.media.delete_image') as delete:
            cleanup_images(now=datetime.utcnow() + timedelta(days=3))
            delete.assert_not_called()

    def test_upload_optimizes_cleaned_image_that_expands_past_limit(self):
        import io
        from PIL import Image
        from app.media import prepare_image
        # Highly compressed input expands when sanitized at normal JPEG quality.
        picture = Image.effect_noise((512, 512), 100).convert('RGB')
        raw = io.BytesIO(); picture.save(raw, 'JPEG', quality=5)
        limit = 80 * 1024
        self.assertLess(len(raw.getvalue()), limit)
        with patch('app.media.MAX_IMAGE_BYTES', limit):
            clean, extension, mime, width, height = prepare_image(raw.getvalue())
        self.assertLessEqual(len(clean), limit)
        self.assertEqual(extension, 'webp')
        self.assertEqual(mime, 'image/webp')
        self.assertLessEqual(width, 512)
        Image.open(io.BytesIO(clean)).verify()

    def device(self):
        response = self.client.post('/api/device-logins/', headers=self.admin_headers, json={})
        self.assertEqual(response.status_code, 201, response.get_json())
        value = response.get_json(); self.assertNotIn('token', value)
        return value, '/api/device-logins/{}/'.format(value['id'])

    def test_device_requires_desktop_approval_and_only_claiming_phone_redeems_once(self):
        value, path = self.device(); secret = 'p' * 48
        self.assertEqual(self.client.post(path + 'claim/', json={'scan_token': 'x' * 48, 'client_secret': secret}).status_code, 403)
        claim = self.client.post(path + 'claim/', json={'scan_token': value['scan_token'], 'client_secret': secret})
        self.assertEqual(claim.status_code, 200, claim.get_json())
        self.assertEqual(self.client.post(path + 'claim/', json={'scan_token': value['scan_token'], 'client_secret': 'b' * 48}).status_code, 409)
        waiting = self.client.post(path + 'consume/', json={'client_secret': secret}).get_json()
        self.assertEqual(waiting['status'], 'scanned'); self.assertNotIn('token', waiting)
        self.assertEqual(self.client.patch(path, headers=self.headers, json={'code': claim.get_json()['code']}).status_code, 404)
        self.assertEqual(self.client.patch(path, headers=self.admin_headers, json={'code': 'wrong'}).status_code, 400)
        self.assertEqual(self.client.patch(path, headers=self.admin_headers, json={'code': claim.get_json()['code']}).status_code, 200)
        self.assertEqual(self.client.post(path + 'consume/', json={'client_secret': 'b' * 48}).status_code, 403)
        result = self.client.post(path + 'consume/', json={'client_secret': secret})
        self.assertEqual(result.status_code, 200, result.get_json())
        token = result.get_json()['token']
        self.assertEqual(self.client.get('/api/get-self/', headers={'Authorization': token}).get_json()['id'], self.owner_id)
        self.assertEqual(self.client.post(path + 'consume/', json={'client_secret': secret}).status_code, 410)
        self.assertEqual(result.headers['Cache-Control'], 'no-store')

    def test_device_expiry_revocation_and_bad_payload(self):
        value, path = self.device()
        session = DeviceLogin.query.get(value['id']); session.expires_at = datetime.utcnow() - timedelta(seconds=1); db.session.commit()
        self.assertEqual(self.client.post(path + 'claim/', json={'scan_token': value['scan_token'], 'client_secret': 'p' * 48}).status_code, 410)
        value, path = self.device(); self.device()
        self.assertEqual(self.client.post(path + 'claim/', json={'scan_token': value['scan_token'], 'client_secret': 'p' * 48}).status_code, 410)
        self.assertEqual(self.client.post(path + 'claim/', json=[]).status_code, 400)

    def test_migration_round_trip_preserves_existing_users(self):
        for model in (DeviceLogin, AlbumItem, AlbumPhoto, Album): model.__table__.drop(db.engine)
        path = Path(__file__).resolve().parents[1] / 'migrations/versions/20260919_albums_device_login.py'
        spec = importlib.util.spec_from_file_location('album_migration', path)
        migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
        with db.engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade(); migration.downgrade(); migration.upgrade()
        spec = importlib.util.spec_from_file_location('album_visibility_migration', Path(__file__).resolve().parents[1] / 'migrations/versions/20260919_album_photo_visibility.py')
        visibility = importlib.util.module_from_spec(spec); spec.loader.exec_module(visibility)
        with db.engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)): visibility.upgrade()
        self.assertEqual(self.owner.username, 'owner')
        self.assertEqual(self.album()['visibility'], 'private')


if __name__ == '__main__': unittest.main()
