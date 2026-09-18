import io
import os
os.environ.setdefault("FLASK_POSTS_PER_PAGE", "10")
os.environ.setdefault("FLASK_BBS_PER_PAGE", "10")
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from flask import Flask
from PIL import Image
from app.image_tools import cloud, migration
from app.generation.providers import save_image


class ImageCloudTests(unittest.TestCase):
    def test_signing_is_https_scoped_short_lived_and_filename_is_encoded(self):
        app = Flask(__name__)
        app.config.update(IMAGE_TOOLS_QINIU_BUCKET='private-test', IMAGE_TOOLS_QINIU_REGION='cn-south-1',
                          QI_NIU_ACCESS_KEY='test-ak', QI_NIU_SECRET_KEY='test-sk')
        with app.app_context():
            url = urlsplit(cloud.signed_url('image-tools/example.png', filename='照片.png'))
            query = parse_qs(url.query)
            self.assertEqual(url.scheme, 'https')
            self.assertEqual(url.netloc, 's3.cn-south-1.qiniucs.com')
            self.assertEqual(url.path, '/private-test/image-tools/example.png')
            self.assertEqual(query['X-Amz-Expires'], ['60'])
            self.assertNotIn('test-sk', url.geturl())
            self.assertIn('attachment', query['response-content-disposition'][0])

    def test_migration_never_removes_file_when_cloud_verification_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / ('a' * 32 + '.png'); file.write_bytes(b'original')
            with patch.object(cloud, 'put'), patch.object(cloud, 'read', return_value=b'corrupt'):
                with self.assertRaises(RuntimeError): migration.migrate(folder, delete_source=True)
            self.assertEqual(file.read_bytes(), b'original')
            with patch.object(cloud, 'put'), patch.object(cloud, 'read', return_value=b'original'):
                self.assertEqual(migration.migrate(folder, delete_source=True), 1)
            self.assertFalse(file.exists())

    def test_generated_news_cover_is_saved_to_cloud_not_disk(self):
        data=io.BytesIO(); Image.new('RGB', (256,128), 'blue').save(data,'PNG')
        with patch.object(cloud,'put') as put, patch.object(Path,'write_bytes',side_effect=AssertionError('Disk write')):
            asset=save_image(data.getvalue(),'request')
        self.assertEqual(put.call_args.args[0], 'generation-artifacts/'+asset['sha256']+'.png')
        self.assertEqual(put.call_args.args[1], data.getvalue())
