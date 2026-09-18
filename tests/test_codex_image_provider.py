"""Image account isolation and native artifact handling; no live services."""
import base64
import hashlib
import json
import os
import struct
import subprocess
import unittest
import zlib
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from flask import Flask

os.environ.setdefault('FLASK_POSTS_PER_PAGE', '10')
os.environ.setdefault('FLASK_BBS_PER_PAGE', '10')
from app.generation import codex_provider, providers
from app.generation.configuration import default_config, validate_config, GenerationError


def png():
    def chunk(kind, value):
        return struct.pack('>I', len(value)) + kind + value + struct.pack('>I', zlib.crc32(kind + value) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 256, 128, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress((b'\0' + b'\xff' * 256 * 3) * 128)) + chunk(b'IEND', b''))


class CodexImageProviderTests(unittest.TestCase):
    def setUp(self):
        cloud = patch('app.image_tools.cloud.put')
        self.cloud_put = cloud.start()
        self.addCleanup(cloud.stop)
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        (self.root / 'auth.json').write_text(json.dumps({'auth_mode': 'chatgpt', 'tokens': {'access_token': 'fixture'}}))
        self.environment = patch.dict(os.environ, {'CONTENT_IMAGE_CODEX_HOME': str(self.root),
            'CONTENT_ASSET_DIR': str(self.root / 'assets'), 'CONTENT_CODEX_BASE_URL': 'https://text.example/v1',
            'CODEX_API_KEY': 'text-key', 'OPENAI_API_KEY': 'text-key', 'DATABASE_URL': 'private-db', 'QI_NIU_SECRET_KEY': 'private-storage'})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.binary = patch.object(codex_provider, 'executable', return_value='/usr/bin/codex')
        self.binary.start()
        self.addCleanup(self.binary.stop)
        self.config = default_config()
        self.model = self.config['image_model']
        self.model.update(provider='codex', timeout=600)

    def event_result(self, thread='image-thread'):
        return SimpleNamespace(returncode=0, stdout=json.dumps({'type': 'thread.started', 'thread_id': thread}) + '\n')

    def test_native_image_uses_separate_account_and_preserves_validated_asset(self):
        def run(command, **kwargs):
            self.assertIn('features.image_generation=true', command)
            self.assertIn('--ignore-user-config', command)
            self.assertFalse(any('base_url' in arg for arg in command))
            self.assertEqual(kwargs['env']['CODEX_HOME'], str(self.root))
            for name in ['CODEX_API_KEY', 'OPENAI_API_KEY', 'DATABASE_URL', 'QI_NIU_SECRET_KEY', 'CONTENT_CODEX_BASE_URL']:
                self.assertNotIn(name, kwargs['env'])
            self.assertEqual(kwargs['timeout'], 600)
            native = self.root / 'generated_images/image-thread/call.png'
            native.parent.mkdir(parents=True)
            native.write_bytes(png())
            return self.event_result()
        with patch.object(codex_provider.subprocess, 'run', side_effect=run):
            asset, metadata = providers.generate_image(self.config, '蓝白概念插图', 'request-1')
        self.assertEqual((asset['width'], asset['height']), (256, 128))
        self.assertEqual(asset['sha256'], hashlib.sha256(png()).hexdigest())
        self.assertEqual(self.cloud_put.call_args.args, ('generation-artifacts/' + asset['filename'], png()))
        self.assertFalse((self.root / 'generated_images/image-thread/call.png').exists())
        self.assertEqual(metadata['native_image_tool'], 'image_gen.imagegen')

    def test_missing_or_api_key_login_cannot_start_a_paid_generation(self):
        for value in ['{}', '{broken', json.dumps({'auth_mode': 'apikey', 'tokens': {'access_token': 'old'}})]:
            (self.root / 'auth.json').write_text(value)
            with patch.object(codex_provider.subprocess, 'run') as run:
                self.assertFalse(codex_provider.image_login_ready())
                with self.assertRaises(GenerationError) as error:
                    codex_provider.generate_image(self.model, 'cover', 'request-1')
                self.assertEqual(error.exception.code, 'codex_image_login_missing')
                run.assert_not_called()

    def test_arbitrary_reported_path_and_other_threads_are_never_used(self):
        other = self.root / 'generated_images/other-thread/call.png'
        other.parent.mkdir(parents=True)
        other.write_bytes(png())
        def run(command, **kwargs):
            Path(command[command.index('--output-last-message') + 1]).write_text(str(other))
            return self.event_result()
        with patch.object(codex_provider.subprocess, 'run', side_effect=run):
            with self.assertRaises(GenerationError) as error:
                codex_provider.generate_image(self.model, 'cover', 'request-1')
            self.assertEqual(error.exception.code, 'codex_image_missing')

    def test_invalid_native_file_and_path_escape_are_rejected(self):
        directory = self.root / 'generated_images/image-thread'
        directory.mkdir(parents=True)
        output = directory / 'call.png'
        output.write_bytes(b'not a PNG')
        with patch.object(codex_provider.subprocess, 'run', return_value=self.event_result()):
            with self.assertRaises(GenerationError):
                codex_provider.generate_image(self.model, 'cover', 'request-1')
            self.assertFalse(output.exists())
            outside = self.root / 'unrelated.png'
            outside.write_bytes(png())
            output.symlink_to(outside)
            with self.assertRaises(GenerationError):
                codex_provider.generate_image(self.model, 'cover', 'request-1')
        with patch.object(codex_provider.subprocess, 'run', return_value=self.event_result('../escape')):
            with self.assertRaises(GenerationError):
                codex_provider.generate_image(self.model, 'cover', 'request-1')

    def test_image_timeout_is_retryable(self):
        with patch.object(codex_provider.subprocess, 'run', side_effect=subprocess.TimeoutExpired('codex', 600)):
            with self.assertRaises(GenerationError) as error:
                codex_provider.generate_image(self.model, 'cover', 'request-1')
            self.assertEqual(error.exception.code, 'codex_image_timeout')
            self.assertTrue(error.exception.retryable)

    def test_readiness_uses_image_login_instead_of_image_api_credentials(self):
        app = Flask(__name__)
        app.config.update({k: 'fixture' for k in ['QI_NIU_ACCESS_KEY', 'QI_NIU_SECRET_KEY', 'QI_NIU_BUCKET', 'QI_NIU_LINK_URL']})
        self.config['text_model']['provider'] = 'codex'
        with app.app_context():
            self.assertEqual(providers.readiness(self.config), [])
            (self.root / 'auth.json').unlink()
            self.assertEqual(providers.readiness(self.config), ['CONTENT_IMAGE_CODEX_LOGIN'])
            self.assertEqual(providers.readiness(self.config, reuse_cover=True), [])

    def test_legacy_api_config_and_png_response_remain_supported(self):
        config = default_config()
        config['image_model'].pop('provider')
        self.assertEqual(validate_config(config)['image_model']['provider'], 'openai_compatible')
        with patch.object(providers, 'call', return_value=({'data': [{'b64_json': base64.b64encode(png()).decode()}]}, {'provider': 'api'})) as call:
            asset, metadata = providers.generate_image(config, 'cover', 'request-1')
        self.assertEqual(call.call_args[0][1], '/images/generations')
        self.assertEqual(asset['width'], 256)
        self.assertEqual(metadata['provider'], 'api')
        config['image_model']['provider'] = 'unsupported'
        with self.assertRaises(ValueError):
            validate_config(config)


if __name__ == '__main__':
    unittest.main()
