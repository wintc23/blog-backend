"""Credential rotation, revocation and private CPA configuration preservation."""
import json
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
import unittest
from scripts.sync_cpa_codex import synchronize


class CpaCredentialSyncTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.auth = self.root / 'auth.json'
        self.auth.write_text(json.dumps({'OPENAI_API_KEY': 'new-fixture'}))
        self.config = self.root / 'config.yaml'
        self.config.write_text(json.dumps({'host': '127.0.0.1', 'api-keys': ['downstream-fixture'],
            'openai-compatibility': [{'name': 'server-codex', 'base-url': 'https://example.com/v1',
                'models': [{'name': 'gpt-image-2.5-flare', 'image': True}],
                'api-key-entries': [{'api-key': 'old-fixture', 'proxy-url': 'direct'}]}]}))
        self.config.chmod(0o640)

    def test_rotation_preserves_source_endpoint_models_and_downstream_key(self):
        source = self.auth.read_bytes()
        self.assertTrue(synchronize(self.auth, self.config))
        config = json.loads(self.config.read_text())
        provider = config['openai-compatibility'][0]
        self.assertEqual(provider['api-key-entries'][0]['api-key'], 'new-fixture')
        self.assertEqual(provider['base-url'], 'https://example.com/v1')
        self.assertEqual(provider['models'][0]['name'], 'gpt-image-2.5-flare')
        self.assertEqual(config['api-keys'], ['downstream-fixture'])
        self.assertEqual(self.auth.read_bytes(), source)
        self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o640)
        self.assertEqual(list(self.root.glob('.codex-sync-*')), [])
        self.assertFalse(synchronize(self.auth, self.config))

    def test_logout_disables_route_and_relogin_restores_it(self):
        self.auth.unlink()
        self.assertTrue(synchronize(self.auth, self.config))
        provider = json.loads(self.config.read_text())['openai-compatibility'][0]
        self.assertTrue(provider['disabled'])
        self.assertEqual(provider['api-key-entries'], [])
        self.auth.write_text(json.dumps({'OPENAI_API_KEY': 'next-fixture'}))
        self.assertTrue(synchronize(self.auth, self.config))
        provider = json.loads(self.config.read_text())['openai-compatibility'][0]
        self.assertFalse(provider['disabled'])
        self.assertEqual(provider['api-key-entries'][0]['api-key'], 'next-fixture')

    def test_oauth_or_invalid_auth_never_reuses_an_old_api_key(self):
        for auth in ['{broken', '[]', '{"tokens":{"access_token":"oauth-fixture"}}']:
            with self.subTest(auth=auth):
                self.auth.write_text(auth)
                synchronize(self.auth, self.config)
                provider = json.loads(self.config.read_text())['openai-compatibility'][0]
                self.assertTrue(provider['disabled'])
                self.assertEqual(provider['api-key-entries'], [])

    def test_unknown_provider_does_not_overwrite_configuration(self):
        self.config.write_text('{"openai-compatibility": []}')
        before = self.config.read_bytes()
        with self.assertRaises(ValueError):
            synchronize(self.auth, self.config)
        self.assertEqual(self.config.read_bytes(), before)
