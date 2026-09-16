"""Codex process boundary tests; no model or external service calls."""
import json
import os
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('FLASK_POSTS_PER_PAGE', '10')
os.environ.setdefault('FLASK_BBS_PER_PAGE', '10')
from app.generation.codex_provider import generate
from app.generation.configuration import GenerationError


class CodexProviderTests(unittest.TestCase):
    def test_cli_uses_stdin_schema_and_restricted_environment(self):
        model = {'model': '', 'timeout': 120}
        def run(command, **kwargs):
            self.assertIsInstance(command, list)
            self.assertNotIn('shell', kwargs)
            self.assertEqual(command[command.index('--sandbox') + 1], 'read-only')
            self.assertIn('features.shell_tool=false', command)
            self.assertIn('--ignore-user-config', command)
            self.assertIn('openai_base_url="https://provider.example/v1"', command)
            self.assertNotIn('DATABASE_URL', kwargs['env'])
            self.assertNotIn('QI_NIU_SECRET_KEY', kwargs['env'])
            schema = json.loads(Path(command[command.index('--output-schema') + 1]).read_text())
            self.assertEqual(set(schema['required']), {'passed', 'issues'})
            self.assertIn('untrusted source', kwargs['input'])
            Path(command[command.index('--output-last-message') + 1]).write_text('{"passed":true,"issues":[]}')
            return SimpleNamespace(returncode=0, stdout='{"type":"turn.completed","usage":{"output_tokens":10}}\n')
        with patch('app.generation.codex_provider.executable', return_value='/usr/bin/codex'), \
             patch('app.generation.codex_provider.subprocess.run', side_effect=run), \
             patch.dict(os.environ, {'DATABASE_URL': 'private-test-value', 'QI_NIU_SECRET_KEY': 'private-test-value',
                                     'CONTENT_CODEX_BASE_URL': 'https://provider.example/v1'}):
            result, metadata = generate(model, 'review', {'article': 'untrusted source'}, 'request-1')
        self.assertTrue(result['passed'])
        self.assertEqual(metadata['usage']['output_tokens'], 10)

    def test_provider_override_rejects_embedded_credentials(self):
        with patch('app.generation.codex_provider.executable', return_value='/usr/bin/codex'), \
             patch.dict(os.environ, {'CONTENT_CODEX_BASE_URL': 'https://user:secret@provider.example/v1'}), \
             patch('app.generation.codex_provider.subprocess.run') as run:
            with self.assertRaises(GenerationError):
                generate({'model': '', 'timeout': 30}, 'review', {'article': ''}, 'request-1')
            run.assert_not_called()

    def test_timeout_is_retryable_and_error_does_not_expose_process_output(self):
        with patch('app.generation.codex_provider.executable', return_value='/usr/bin/codex'), \
             patch('app.generation.codex_provider.subprocess.run', side_effect=subprocess.TimeoutExpired('codex', 1)):
            with self.assertRaises(GenerationError) as error:
                generate({'model': '', 'timeout': 30}, 'review', {'article': ''}, 'request-1')
        self.assertEqual(error.exception.code, 'codex_timeout')
        self.assertTrue(error.exception.retryable)


if __name__ == '__main__':
    unittest.main()
