"""Administrator task inspection is read-only and never opens owner APIs."""
import json
import unittest
from datetime import datetime
import test_image_tools as fixture
from app import db
from app.image_tool_models import ImageTask, ImageToolAsset, ImageTool

class ImageAdminJobsTests(unittest.TestCase):
    owner = fixture.ImageToolsTests.owner
    regular = fixture.ImageToolsTests.regular
    create = fixture.ImageToolsTests.create
    upload = fixture.ImageToolsTests.upload
    cloud_upload = fixture.ImageToolsTests.cloud_upload
    png = fixture.ImageToolsTests.png
    def setUp(self):
        fixture.ImageToolsTests.setUp(self)
        self.task_id = self.create(); self.input_id = self.upload(self.task_id)
        self.output_id = self.upload(self.task_id)
        ImageToolAsset.query.get(self.output_id).kind = 'output'
        task = ImageTask.query.get(self.task_id); task.status = 'completed'
        task.options_json = json.dumps({'prompt': 'private task prompt', 'ratio': '3:2', 'fields': {'style': 'ink'}})
        config = json.loads(task.snapshot_json); config['instruction'] = 'original task instruction'; task.snapshot_json = json.dumps(config)
        db.session.commit()
        self.path = '/api/image-tools/admin/jobs/{}/'.format(self.task_id)
    def tearDown(self): fixture.ImageToolsTests.tearDown(self)
    def test_only_admin_can_inspect_others_images_and_configuration(self):
        guest = self.client.post('/api/guest-login/', json={}).get_json()
        for headers, status in (({}, 401), (self.headers, 403), ({'Authorization': guest['token']}, 403)):
            self.assertEqual(self.client.get('/api/image-tools/admin/jobs/', headers=headers).status_code, status)
            self.assertEqual(self.client.get(self.path, headers=headers).status_code, status)
        tool = ImageTool.query.filter_by(slug='cartoon').one()
        config = json.loads(tool.config_json); config['instruction'] = 'new instruction'; tool.config_json = json.dumps(config); db.session.commit()
        result = self.client.get(self.path, headers=self.admin_headers)
        self.assertEqual(result.status_code, 200, result.get_json())
        data = result.get_json(); task = data['task']
        self.assertEqual(data['owner']['id'], self.regular_id)
        self.assertEqual(task['options']['prompt'], 'private task prompt')
        self.assertEqual(task['config']['instruction'], 'original task instruction')
        self.assertEqual([a['id'] for a in task['inputs']], [self.input_id])
        self.assertEqual([a['id'] for a in task['outputs']], [self.output_id])
        self.assertNotIn('quota', task)
        for asset in task['inputs'] + task['outputs']:
            response = self.client.get('/api'+asset['url']); self.assertEqual(response.status_code, 302)
            self.assertIn('qiniucs.com', response.location)
        self.assertEqual(self.client.get('/api/image-tasks/{}/'.format(self.task_id), headers=self.admin_headers).status_code, 404)
        self.assertEqual(self.client.patch('/api/image-tasks/{}/'.format(self.task_id), headers=self.admin_headers, json={'options': {}}).status_code, 404)
        self.assertEqual(self.client.get('/api/image-tasks/{}/'.format(self.task_id), headers=self.headers).status_code, 200)
    def test_pagination_excludes_drafts_and_deleted_tasks(self):
        draft_id = self.create(); deleted_id = self.create(); second_id = self.create()
        deleted = ImageTask.query.get(deleted_id); deleted.status = 'completed'; deleted.deleted_at = datetime.utcnow()
        ImageTask.query.get(second_id).status = 'failed'; db.session.commit()
        found = []
        for page in (1, 2):
            response = self.client.get('/api/image-tools/admin/jobs/?per_page=1&page='+str(page), headers=self.admin_headers)
            data = response.get_json(); self.assertEqual(data['total'], 2); self.assertEqual(len(data['jobs']), 1)
            self.assertEqual(data['jobs'][0]['owner_name'], 'member'); found.append(data['jobs'][0]['id'])
        self.assertEqual(set(found), {self.task_id, second_id})
        for task_id in (draft_id, deleted_id, 'missing'):
            self.assertEqual(self.client.get('/api/image-tools/admin/jobs/{}/'.format(task_id), headers=self.admin_headers).status_code, 404)
        self.assertEqual(self.client.get('/api/image-tools/admin/jobs/?page=invalid', headers=self.admin_headers).status_code, 400)

if __name__ == '__main__': unittest.main()
