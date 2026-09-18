"""Digest migration/privacy/integrity checks against isolated SQLite only."""
import importlib.util
import json
import os
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
import unittest
from copy import deepcopy
from tempfile import TemporaryDirectory
from unittest.mock import patch

os.environ.setdefault('FLASK_POSTS_PER_PAGE', '10')
os.environ.setdefault('FLASK_BBS_PER_PAGE', '10')
from flask import g, request
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app import create_app, db
from app.digest_models import AiDigest, AiDigestSettings, AiNewsItem, AiNewsSource


def migration(name='20260916_ai_digest'):
    path = Path(__file__).parents[1] / 'migrations/versions' / (name + '.py')
    spec = importlib.util.spec_from_file_location('digest_migration', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DigestTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app('testing')
        self.app.config.update(SQLALCHEMY_DATABASE_URI='sqlite://', TESTING=True)
        def identity():
            role = request.headers.get('X-Test-Role')
            if role:
                g.current_user = SimpleNamespace(can=lambda p: role == 'admin')
        self.app.before_request_funcs.setdefault('api', []).append(identity)
        self.context = self.app.app_context()
        self.context.push()
        from app.media_models import MediaAsset, MediaReference
        MediaAsset.__table__.create(db.engine)
        MediaReference.__table__.create(db.engine)
        with db.engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration().upgrade()
                migration('20260916_digest_reads').upgrade()
        self.client = self.app.test_client()
        self.clock = patch('app.api.ai_digest._utcnow', return_value=datetime(2026, 9, 16, 12))
        self.clock.start()

    def tearDown(self):
        self.clock.stop()
        db.session.remove()
        db.engine.dispose()
        self.context.pop()

    def issue(self, day, status):
        now = datetime.utcnow()
        row = AiDigest(issue_date=day, timezone='Asia/Shanghai', slug=day.isoformat(),
            title='测试快讯', summary='简报', content_json=json.dumps({'schema_version': 1, 'sections': []}),
            scheduled_publish_at=datetime.combine(day, datetime.min.time()).replace(hour=1),
            published_at=datetime.combine(day, datetime.min.time()).replace(hour=1) if status == 'published' else None,
            status=status, source_window_start=now, source_window_end=now, created_at=now, updated_at=now)
        db.session.add(row)
        db.session.commit()
        return row

    def test_drafts_are_private_and_admin_can_preview(self):
        draft_id = self.issue(date(2026, 9, 15), 'draft').id
        published_id = self.issue(date(2026, 9, 16), 'published').id
        response = self.client.get('/api/ai-digests/').get_json()
        self.assertEqual([item['id'] for item in response['list']], [published_id])
        self.assertEqual(self.client.get('/api/ai-digests/{}/'.format(draft_id)).status_code, 404)
        self.assertEqual(self.client.get('/api/ai-digests/?manage=1').status_code, 401)
        self.assertEqual(self.client.get('/api/ai-digests/?manage=1', headers={'X-Test-Role': 'user'}).status_code, 403)
        self.assertEqual(self.client.get('/api/ai-digests/{}/'.format(draft_id), headers={'X-Test-Role': 'admin'}).get_json()['content']['sections'], [])
        self.assertEqual(self.client.get('/api/ai-news-items/').status_code, 401)

    def test_same_day_cannot_create_a_second_issue(self):
        self.issue(date(2026, 9, 16), 'draft')
        with self.assertRaises(IntegrityError):
            self.issue(date(2026, 9, 16), 'draft')
        db.session.rollback()
        self.assertEqual(AiDigest.query.count(), 1)

    def test_pagination_validation(self):
        for query in ('page=0', 'page=bad', 'per_page=999'):
            self.assertEqual(self.client.get('/api/ai-digests/?' + query).status_code, 400)

    def channel(self):
        row = AiDigestSettings(id=1, title='栏目', preferences_json=json.dumps({'private_note': 'editor only'}),
            generator_json='{}', prompt_template='private prompt', updated_at=datetime.utcnow())
        db.session.add(row)
        db.session.commit()
        return row

    def introduction(self):
        return {'summary': '每日整理。', 'note': '附原始来源。', 'groups': [
            {'id': 'applications', 'title': '应用', 'description': '新功能。'},
            {'id': 'development', 'title': '开发', 'description': '模型进展。'}]}

    def test_channel_copy_is_admin_managed_and_public_projection_is_safe(self):
        row = self.channel()
        before = (row.prompt_template, row.publish_time, row.auto_publish)
        path = '/api/generation/channel/'
        payload = {'title': '新栏目名称', 'introduction': self.introduction()}
        for method in (self.client.get, self.client.put):
            self.assertEqual(method(path, json=payload).status_code, 401)
            self.assertEqual(method(path, json=payload, headers={'X-Test-Role': 'user'}).status_code, 403)
        response = self.client.put(path, json=payload, headers={'X-Test-Role': 'admin'})
        self.assertEqual(response.status_code, 200)
        for endpoint in ('/api/ai-digests/home/', '/api/ai-digests/'):
            settings = self.client.get(endpoint).get_json()['settings']
            self.assertEqual(settings['introduction'], payload['introduction'])
            self.assertEqual(settings['title'], payload['title'])
            self.assertEqual(set(settings), {'title', 'timezone', 'publish_time', 'introduction'})
        row = AiDigestSettings.query.get(1)
        self.assertEqual(json.loads(row.preferences_json)['private_note'], 'editor only')
        self.assertEqual((row.prompt_template, row.publish_time, row.auto_publish), before)

    def test_invalid_channel_copy_does_not_modify_saved_settings(self):
        row = self.channel()
        self.assertIsNone(self.client.get('/api/ai-digests/home/').get_json()['settings']['introduction'])
        invalid = [None, {'summary': 'x', 'note': '', 'groups': []}, self.introduction()]
        invalid[-1]['summary'] = 'x' * 201
        for introduction in invalid:
            response = self.client.put('/api/generation/channel/', json={'title': 'changed', 'introduction': introduction}, headers={'X-Test-Role': 'admin'})
            self.assertEqual(response.status_code, 400)
        row = AiDigestSettings.query.get(1)
        self.assertEqual(row.title, '栏目')
        self.assertEqual(json.loads(row.preferences_json), {'private_note': 'editor only'})

    def test_get_requests_and_prefetch_never_count_reads(self):
        digest_id = self.issue(date(2026, 9, 16), 'published').id
        for path in ('/api/ai-digests/', '/api/ai-digests/home/', '/api/ai-digests/{}/'.format(digest_id)):
            for headers in ({}, {'Purpose': 'prefetch'}, {'X-Test-Role': 'admin'}):
                self.assertEqual(self.client.get(path, headers=headers).status_code, 200)
        self.assertEqual(AiDigest.query.get(digest_id).read_times, 0)
        self.assertEqual(self.client.get('/api/ai-digests/').get_json()['list'][0]['read_times'], 0)
        self.assertEqual(self.client.get('/api/ai-digests/home/').get_json()['featured']['read_times'], 0)

    def test_read_counts_visitors_and_preserves_content_metadata(self):
        issue = self.issue(date(2026, 9, 16), 'published')
        digest_id = issue.id
        before = (issue.content_json, issue.content_version, issue.updated_at, issue.published_at)
        path = '/api/ai-digests/{}/read/'.format(digest_id)
        self.assertEqual(self.client.post(path).get_json(), {'read_times': 1, 'counted': True})
        self.assertEqual(self.client.post(path, headers={'X-Test-Role': 'user'}).get_json(), {'read_times': 2, 'counted': True})
        issue = AiDigest.query.get(digest_id)
        self.assertEqual((issue.content_json, issue.content_version, issue.updated_at, issue.published_at), before)
        self.assertEqual(self.client.get('/api/ai-digests/home/').get_json()['featured']['read_times'], 2)

    def test_owner_and_unrecognised_credentials_do_not_count(self):
        digest_id = self.issue(date(2026, 9, 16), 'published').id
        path = '/api/ai-digests/{}/read/'.format(digest_id)
        # Exercise the real Authorization path, not a client-provided admin flag.
        with patch('app.models.User.verify_auth_token', return_value=SimpleNamespace(can=lambda p: True)):
            self.assertEqual(self.client.post(path, headers={'Authorization': 'owner-token'}).get_json(),
                             {'read_times': 0, 'counted': False})
        self.assertEqual(self.client.post(path, headers={'Authorization': 'expired-token'}).get_json(),
                         {'read_times': 0, 'counted': False})
        self.assertEqual(self.client.post(path, json={'admin': True}).get_json(), {'read_times': 1, 'counted': True})

    def test_unpublished_future_and_withdrawn_issues_cannot_be_counted(self):
        for day, status in ((12, 'draft'), (13, 'withdrawn'), (17, 'published')):
            digest_id = self.issue(date(2026, 9, day), status).id
            path = '/api/ai-digests/{}/read/'.format(digest_id)
            for headers in ({}, {'X-Test-Role': 'admin'}):
                self.assertEqual(self.client.post(path, headers=headers).status_code, 404)
            self.assertEqual(AiDigest.query.get(digest_id).read_times, 0)
        self.assertEqual(self.client.post('/api/ai-digests/9999/read/').status_code, 404)

    def test_read_count_migration_backfills_existing_issues(self):
        digest_id = self.issue(date(2026, 9, 16), 'published').id
        db.session.remove()
        with db.engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration('20260916_digest_reads').downgrade()
                self.assertNotIn('read_times', {c['name'] for c in inspect(connection).get_columns('ai_digests')})
                migration('20260916_digest_reads').upgrade()
        self.assertEqual(AiDigest.query.get(digest_id).read_times, 0)

    def test_home_prefers_today_and_limits_distinct_history_to_five(self):
        for day in range(6, 17):
            self.issue(date(2026, 9, day), 'published')
        self.issue(date(2026, 9, 17), 'published')
        self.issue(date(2026, 9, 18), 'draft')
        result = self.client.get('/api/ai-digests/home/').get_json()
        self.assertTrue(result['is_today'])
        self.assertEqual(result['featured']['issue_date'], '2026-09-16')
        self.assertEqual([r['issue_date'] for r in result['previous']],
                         ['2026-09-15', '2026-09-14', '2026-09-13', '2026-09-12', '2026-09-11'])
        self.assertNotIn(result['featured']['id'], [r['id'] for r in result['previous']])

    def test_home_fallback_empty_and_beijing_nine_am_boundary(self):
        empty = self.client.get('/api/ai-digests/home/').get_json()
        self.assertIsNone(empty['featured'])
        self.assertEqual(empty['previous'], [])
        self.issue(date(2026, 9, 15), 'published')
        self.issue(date(2026, 9, 16), 'published')
        with patch('app.api.ai_digest._utcnow', return_value=datetime(2026, 9, 15, 16, 30)):
            early = self.client.get('/api/ai-digests/home/').get_json()
            self.assertEqual(early['today'], '2026-09-16')
            self.assertFalse(early['is_today'])
            self.assertEqual(early['featured']['issue_date'], '2026-09-15')
        with patch('app.api.ai_digest._utcnow', return_value=datetime(2026, 9, 16, 1)):
            self.assertTrue(self.client.get('/api/ai-digests/home/').get_json()['is_today'])

    def test_archive_pagination_and_withdrawn_visibility(self):
        for day in range(10, 17):
            self.issue(date(2026, 9, day), 'published')
        withdrawn_id = self.issue(date(2026, 9, 9), 'withdrawn').id
        result = self.client.get('/api/ai-digests/?page=2&per_page=2').get_json()
        self.assertEqual(result['total'], 7)
        self.assertEqual([r['issue_date'] for r in result['list']], ['2026-09-14', '2026-09-13'])
        self.assertEqual(self.client.get('/api/ai-digests/{}/'.format(withdrawn_id)).status_code, 404)

    def test_backfill_rejects_wrong_time_and_future_source(self):
        from import_ai_digests import validate
        rows = [{'issue_date': '2026-09-12', 'timezone': 'Asia/Shanghai',
            'scheduled_publish_at': '2026-09-12T01:00:00Z', 'source_window_end': '2026-09-12T01:00:00Z',
            'source_window_start': '2026-09-09T01:00:00Z', 'content': {
                'schema_version': 1, 'takeaways': ['A sourced update'],
                'cover': {'local_path': 'cover.svg', 'rights': 'original', 'alt': 'Cover'},
                'sections': [{'id': 'story', 'paragraphs': ['Summary'], 'sources': [
                    {'published_date': '2026-09-11', 'url': 'https://example.org/news'}]}]}}]
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'cover.png').write_bytes(b'test image placeholder')
            validate(rows, root)
            wrong_time = deepcopy(rows)
            wrong_time[0]['scheduled_publish_at'] = '2026-09-12T09:00:00Z'
            with self.assertRaises(AssertionError):
                validate(wrong_time, root)
            future = deepcopy(rows)
            future[0]['content']['sections'][0]['sources'][0]['published_date'] = '2026-09-12'
            with self.assertRaises(AssertionError):
                validate(future, root)

    def test_migration_matches_models_and_reverses(self):
        db.session.remove()
        tables = set(inspect(db.engine).get_table_names())
        self.assertEqual(len(set(tables) - {'media_assets', 'media_references'}), 8)
        for name in tables:
            self.assertEqual(set(db.metadata.tables[name].columns.keys()),
                {column['name'] for column in inspect(db.engine).get_columns(name)})
        with db.engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration('20260916_digest_reads').downgrade()
                migration().downgrade()
        self.assertEqual(set(inspect(db.engine).get_table_names()), {'media_assets', 'media_references'})


if __name__ == '__main__':
    unittest.main()
