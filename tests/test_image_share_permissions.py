"""Only administrators can publish results; stale links cannot bypass the policy."""
import hashlib
import json
import unittest
from datetime import datetime, timedelta
import test_image_tools as fixture
from app import db
from app.models import Role, User
from app.image_tool_models import ImageTask, ImageToolAsset
from app.content_like_models import ContentLike
from app.api.image_tools import asset_json

class ImageSharePermissionsTests(unittest.TestCase):
    owner = fixture.ImageToolsTests.owner
    regular = fixture.ImageToolsTests.regular
    create = fixture.ImageToolsTests.create
    upload = fixture.ImageToolsTests.upload
    cloud_upload = fixture.ImageToolsTests.cloud_upload
    png = fixture.ImageToolsTests.png
    def setUp(self):
        fixture.ImageToolsTests.setUp(self)
        ContentLike.__table__.create(db.engine)
        self.task_id = self.create(); self.asset_id = self.upload(self.task_id)
        ImageToolAsset.query.get(self.asset_id).kind = 'output'; db.session.commit()
        self.path = '/api/image-tasks/{}/share/'.format(self.task_id)
    def tearDown(self): fixture.ImageToolsTests.tearDown(self)
    def test_only_admin_owner_can_create_or_revoke(self):
        guest = self.client.post('/api/guest-login/', json={}).get_json()
        for headers, expected in (({}, 401), ({'Authorization': guest['token']}, 403), (self.headers, 403), (self.admin_headers, 404)):
            for method in ('post', 'delete'):
                response = getattr(self.client, method)(self.path, headers=headers, json={'asset_ids': [self.asset_id]})
                self.assertEqual(response.status_code, expected, (method, response.get_json()))
        self.regular.role = self.owner.role; db.session.commit()
        response = self.client.post(self.path, headers=self.headers, json={'asset_ids': [self.asset_id]})
        self.assertEqual(response.status_code, 200, response.get_json())
        token = response.get_json()['token']; path = '/api/image-shares/{}/'.format(token)
        public = self.client.get(path); self.assertEqual(public.status_code, 200)
        signed = '/api' + public.get_json()['outputs'][0]['url']
        self.assertEqual(self.client.get(signed).status_code, 302)
        self.assertEqual(self.client.get(path+'likes/').status_code, 200)
        self.assertEqual(self.client.delete(self.path, headers=self.headers).status_code, 200)
        self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.get(signed).status_code, 403)
    def test_legacy_member_links_and_demoted_admin_links_are_unusable(self):
        token = 'legacy-share-fixture'; digest = hashlib.sha256(token.encode()).hexdigest()
        task = ImageTask.query.get(self.task_id); task.share_hash = digest
        task.share_until = datetime.utcnow() + timedelta(days=1); task.share_assets_json = json.dumps([self.asset_id]); db.session.commit()
        asset = ImageToolAsset.query.get(self.asset_id)
        signed = '/api' + asset_json(asset, digest)['url']; private = '/api' + asset_json(asset)['url']
        path = '/api/image-shares/{}/'.format(token)
        for _ in range(2):
            self.assertEqual(self.client.get(path).status_code, 404)
            self.assertEqual(self.client.get(path+'likes/').status_code, 404)
            self.assertEqual(self.client.get(signed).status_code, 403)
            self.assertEqual(self.client.get(signed+'&resolve=1').status_code, 403)
            own = self.client.get('/api/image-tasks/{}/'.format(self.task_id), headers=self.headers)
            self.assertEqual(own.status_code, 200); self.assertFalse(own.get_json()['task']['sharing'])
            self.assertEqual(self.client.get(private).status_code, 302)
            self.assertEqual(self.client.get('/api/image-tasks/{}/download/'.format(self.task_id), headers=self.headers).status_code, 200)
            member_role_id = self.regular.role_id
            self.regular.role = self.owner.role; db.session.commit()
            self.assertEqual(self.client.get(path).status_code, 200)
            self.regular.role = Role.query.get(member_role_id); db.session.commit()

if __name__ == '__main__': unittest.main()
