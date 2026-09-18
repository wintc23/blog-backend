"""CPA transport boundaries and image pipeline; no live credentials or services."""
import base64
import json
import os
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch
from flask import Flask

os.environ.setdefault('FLASK_POSTS_PER_PAGE', '10')
os.environ.setdefault('FLASK_BBS_PER_PAGE', '10')
from app.generation import network, providers
from app.generation.configuration import default_config, validate_config, GenerationError
from test_codex_image_provider import png


class CpaImageTests(unittest.TestCase):
    def setUp(self):
        cloud = patch('app.image_tools.cloud.put')
        self.cloud_put = cloud.start()
        self.addCleanup(cloud.stop)
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        env = patch.dict(os.environ, {'CONTENT_CPA_API_KEY': 'private-fixture',
            'CONTENT_CPA_BASE_URL': 'http://127.0.0.1:8317/v1', 'CONTENT_ASSET_DIR': directory.name})
        env.start()
        self.addCleanup(env.stop)
        self.config = default_config()
        self.config['text_model']['provider'] = 'codex'
        self.model = self.config['image_model']
        self.model.update(provider='cpa', model='gpt-image-2.5-flare', credential_ref='CONTENT_CPA_API_KEY', timeout=600)

    def test_only_image_provider_accepts_cpa_and_task_cannot_choose_its_url(self):
        self.assertEqual(validate_config(self.config)['image_model']['provider'], 'cpa')
        self.model['base_url'] = 'https://example.com/v1'
        with self.assertRaises(ValueError):
            validate_config(self.config)
        self.model['base_url'] = ''
        self.config['text_model']['provider'] = 'cpa'
        with self.assertRaises(ValueError):
            validate_config(self.config)

    def test_server_endpoint_is_restricted_to_literal_loopback_and_image_path(self):
        for url in ['http://localhost:8317/v1', 'http://10.0.0.1:8317/v1', 'http://127.0.0.1:8317/admin',
                    'https://example.com/v1', 'http://127.0.0.1:8317/v1?key=secret',
                    'http://key@127.0.0.1:8317/v1', 'http://127.0.0.1:8317/v1#x', 'http://127.0.0.1/v1']:
            with self.subTest(url=url), patch.dict(os.environ, {'CONTENT_CPA_BASE_URL': url}):
                with self.assertRaises(ValueError):
                    network.cpa_image_url()
        self.assertEqual(network.cpa_image_url(), 'http://127.0.0.1:8317/v1/images/generations')
        for url in ['http://127.0.0.1:8317/v1', 'https://127.0.0.1/v1']:
            with self.assertRaises(ValueError):
                network.validate_url(url)

    def test_pipeline_preserves_requested_model_and_returns_validated_png(self):
        response = {'model': 'gpt-image-2.5-flare', 'data': [{'b64_json': base64.b64encode(png()).decode()}]}
        with patch.object(providers, 'fetch_cpa_image', return_value=(json.dumps(response).encode(), {'x-request-id': 'upstream-1'})) as fetch:
            asset, metadata = providers.generate_image(self.config, '蓝白概念图', 'image-1')
        payload, headers, timeout, limit = fetch.call_args[0]
        self.assertEqual(payload['model'], 'gpt-image-2.5-flare')
        self.assertEqual(payload['output_format'], 'png')
        self.assertEqual(payload['n'], 1)
        self.assertEqual(headers['Authorization'], 'Bearer private-fixture')
        self.assertEqual(timeout, 600)
        self.assertEqual(asset['width'], 256)
        self.assertEqual(metadata['provider'], 'cpa')
        self.assertEqual(metadata['request_id'], 'upstream-1')
        self.assertNotIn('private-fixture', json.dumps(metadata))

    def test_model_substitution_and_invalid_image_fail(self):
        for response, code in [({'model': 'gpt-image-2', 'data': []}, 'image_model_mismatch'),
                               ({'data': [{'b64_json': 'invalid'}]}, 'invalid_image_response')]:
            with patch.object(providers, 'fetch_cpa_image', return_value=(json.dumps(response).encode(), {})):
                with self.assertRaises(GenerationError) as error:
                    providers.generate_image(self.config, 'cover', 'image-1')
                self.assertEqual(error.exception.code, code)

    def test_redirect_never_forwards_credentials(self):
        response = MagicMock(status_code=302, is_redirect=True)
        response.__enter__.return_value = response
        session = MagicMock()
        session.request.return_value = response
        with patch.object(network.requests, 'Session', return_value=session):
            with self.assertRaises(GenerationError) as error:
                network.fetch_cpa_image({}, {'Authorization': 'Bearer fixture'}, 30, 1000)
        self.assertEqual(error.exception.code, 'provider_redirect')
        self.assertFalse(session.trust_env)
        self.assertFalse(session.request.call_args[1]['allow_redirects'])
        self.assertEqual(session.request.call_count, 1)
        session.close.assert_called_once()

    def test_errors_are_bounded_retryable_and_hide_credentials(self):
        with patch.object(network.requests, 'Session') as create:
            create.return_value.request.side_effect = network.requests.Timeout('private-fixture')
            with self.assertRaises(GenerationError) as error:
                network.fetch_cpa_image({}, {'Authorization': 'Bearer private-fixture'}, 30, 1000)
        self.assertTrue(error.exception.retryable)
        self.assertNotIn('private-fixture', str(error.exception))
        response = MagicMock(status_code=200, is_redirect=False, headers={'Content-Length': '1001'})
        response.__enter__.return_value = response
        with patch.object(network.requests, 'Session') as create:
            create.return_value.request.return_value = response
            with self.assertRaises(GenerationError) as error:
                network.fetch_cpa_image({}, {}, 30, 1000)
        self.assertEqual(error.exception.code, 'response_too_large')

    def test_readiness_uses_cpa_key_without_native_codex_image_login(self):
        app = Flask(__name__)
        app.config.update({k: 'fixture' for k in ['QI_NIU_ACCESS_KEY', 'QI_NIU_SECRET_KEY', 'QI_NIU_BUCKET', 'QI_NIU_LINK_URL']})
        with app.app_context(), patch('app.generation.codex_provider.executable', return_value='/usr/bin/codex'):
            self.assertEqual(providers.readiness(self.config), [])
            with patch.dict(os.environ, {'CONTENT_CPA_API_KEY': ''}):
                self.assertEqual(providers.readiness(self.config), ['CONTENT_CPA_API_KEY'])
            with patch.dict(os.environ, {'CONTENT_CPA_BASE_URL': 'http://10.0.0.1:8317/v1'}):
                self.assertEqual(providers.readiness(self.config), ['CONTENT_CPA_BASE_URL'])
