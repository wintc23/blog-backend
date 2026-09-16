"""Queue/publication integration tests: isolated SQLite, no live model/storage calls."""
import copy
import importlib.util
import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from flask import g, request
from sqlalchemy import inspect, create_engine, text as sql_text
from alembic.migration import MigrationContext
from alembic.operations import Operations

os.environ.setdefault('FLASK_POSTS_PER_PAGE', '10')
os.environ.setdefault('FLASK_BBS_PER_PAGE', '10')
from app import create_app, db
from app.digest_models import AiDigest, AiNewsItem, AiNewsSource
from app.generation_models import GenerationTask as Task, GenerationTaskVersion as Version, GenerationJob as Job, GenerationRun as Run, GeneratedContent as Content, ContentRevision as Revision
from app.generation import engine
from app.generation.configuration import default_config, encode, iso, GenerationError, LeaseLost, validate_config, schedule_for
from app.generation.digest import build_document, validate_document

NOW = datetime(2026, 9, 17, 0, 30)
DAY = NOW.date()
ADMIN = {'X-Test-Role': 'admin'}


def migration(name):
    path = Path(__file__).parents[1] / 'migrations/versions' / (name + '.py')
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app('testing')
        self.app.config.update(SQLALCHEMY_DATABASE_URI='sqlite://', TESTING=True)
        def identity():
            role = request.headers.get('X-Test-Role')
            if role:
                g.current_user = SimpleNamespace(can=lambda permission: role == 'admin')
        self.app.before_request_funcs.setdefault('api', []).append(identity)
        self.context = self.app.app_context()
        self.context.push()
        with db.engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration('20260916_ai_digest').upgrade()
                migration('20260916_generation').upgrade()
                migration('20260916_digest_reads').upgrade()
        self.client = self.app.test_client()
        self.clock = patch('app.generation.engine.utcnow', return_value=NOW)
        self.clock.start()
        self.config = default_config()
        self.config.update(min_chars=100, max_chars=1000, auto_publish=True)
        self.task = Task(name='测试任务', content_type='daily_digest', channel='ai-news', environment='production', enabled=True)
        db.session.add(self.task)
        db.session.flush()
        db.session.add(Version(task_id=self.task.id, version=1, config_json=encode(self.config)))
        source = AiNewsSource(name='Official source', kind='rss', endpoint_url='https://example.com/feed', endpoint_hash=b's' * 32,
            enabled=True, priority=1, config_json='{}', created_at=NOW, updated_at=NOW)
        db.session.add(source)
        db.session.flush()
        item = AiNewsItem(source_id=source.id, canonical_url='https://example.com/news', url_hash=b'u' * 32,
            title='Source update', published_at=NOW - timedelta(hours=1), published_date=DAY,
            date_precision='datetime', first_seen_at=NOW, last_seen_at=NOW, evidence_json='{}', content_hash=b'c' * 32)
        db.session.add(item)
        db.session.commit()
        self.source = {'item_id': item.id, 'publisher': source.name, 'title': item.title, 'url': item.canonical_url,
                       'published_at': iso(item.published_at), 'published_date': DAY.isoformat(), 'excerpt': 'Real source evidence in the isolated test.'}
        self.inputs = {'sources': [self.source], 'source_window_start': iso(NOW - timedelta(hours=72)),
                       'source_window_end': iso(NOW), 'recent_content': []}
        self.raw = {'title': '模型更新与开发流程', 'summary': '测试摘要', 'takeaways': ['测试速览'],
            'sections': [{'id': 'x', 'group_id': 'development', 'category': '模型更新', 'title': '具体变化',
                'paragraphs': ['这是一段测试专用的事实正文，用于验证生成任务的持久化与发布边界。' * 5],
                'analysis': '这是用于验证格式的简评。', 'source_ids': [item.id]}],
            'closing': '测试结语。', 'cover_prompt': '独特的模型概念插图', 'cover_alt': '测试封面'}
        self.doc = build_document(self.raw, self.inputs, self.config, DAY.isoformat())
        self.cover = {'url': 'https://example.com/cover.png', 'alt': '测试封面', 'width': 1200, 'height': 600, 'sha256': 'f' * 64}
        self.doc['content']['cover'].update(self.cover)

    def tearDown(self):
        self.clock.stop()
        db.session.remove()
        db.engine.dispose()
        self.context.pop()

    def job(self, purpose='scheduled'):
        return engine.enqueue(self.task, DAY, purpose, 'manual-request-key-1' if purpose != 'scheduled' else None, NOW)

    def complete(self, purpose='scheduled'):
        self.job(purpose)
        current = engine.claim('production', NOW)
        content_id = engine.persist_result(*current, copy.deepcopy(self.doc))
        return Content.query.get(content_id)

    def test_migration_matches_models_and_reverses(self):
        schema = inspect(db.engine)
        names = ['generation_tasks', 'generation_task_versions', 'generation_jobs', 'generation_runs',
                 'generated_contents', 'content_revisions', 'generation_heartbeats']
        for name in names:
            self.assertEqual(set(db.metadata.tables[name].columns.keys()), {c['name'] for c in schema.get_columns(name)})
        with db.engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration('20260916_generation').downgrade()
        self.assertTrue(all(name not in inspect(db.engine).get_table_names() for name in names))

    def test_admin_only_all_control_routes(self):
        for path in ('/generation/meta/', '/generation/tasks/', '/generation/jobs/', '/generation/sources/', '/generated-contents/'):
            self.assertEqual(self.client.get('/api' + path).status_code, 401)
            self.assertEqual(self.client.get('/api' + path, headers={'X-Test-Role': 'user'}).status_code, 403)
            self.assertEqual(self.client.get('/api' + path, headers=ADMIN).status_code, 200)

    def test_scheduler_idempotency_and_environment(self):
        engine.tick('development', NOW)
        self.assertEqual(Job.query.count(), 0)
        engine.tick('production', NOW - timedelta(seconds=1))
        self.assertEqual(Job.query.count(), 0)
        engine.tick('production', NOW)
        engine.tick('production', NOW)
        self.assertEqual(Job.query.count(), 1)
        self.assertIsNone(engine.claim('development', NOW))
        self.assertIsNotNone(engine.claim('production', NOW))
        self.assertIsNone(engine.claim('production', NOW))

    def test_schedule_is_beijing_and_not_server_local_time(self):
        start, publish, deadline = schedule_for(DAY, self.config)
        self.assertEqual(start, NOW)
        self.assertEqual(publish, datetime(2026, 9, 17, 1))
        self.assertEqual(deadline, datetime(2026, 9, 17, 4))

    def test_lease_recovery_rejects_late_result(self):
        self.job()
        first = engine.claim('production', NOW)
        later = NOW + timedelta(minutes=3)
        engine.tick('production', later)
        second = engine.claim('production', later)
        with patch('app.generation.engine.utcnow', return_value=later):
            with self.assertRaises(LeaseLost):
                engine.persist_result(*first, self.doc)
            engine.persist_result(*second, self.doc)
        self.assertEqual(Content.query.count(), 1)
        self.assertEqual([r.status for r in Run.query.order_by(Run.attempt)], ['failed', 'succeeded'])

    def test_publish_boundary_and_duplicate_publish(self):
        content = self.complete()
        self.assertEqual(AiDigest.query.count(), 0)
        engine.tick('production', datetime(2026, 9, 17, 0, 59, 59))
        self.assertEqual(AiDigest.query.count(), 0)
        engine.tick('production', datetime(2026, 9, 17, 1))
        engine.tick('production', datetime(2026, 9, 17, 1))
        self.assertEqual(AiDigest.query.count(), 1)
        self.assertEqual(AiDigest.query.one().published_at, datetime(2026, 9, 17, 1))

    def test_manual_generation_stays_draft_and_disable_stops_publish(self):
        content = self.complete('test')
        engine.tick('production', datetime(2026, 9, 17, 1))
        self.assertEqual(content.status, 'draft')
        self.assertEqual(AiDigest.query.count(), 0)
        content.status, content.auto_publish = 'ready', True
        self.task.enabled = False
        db.session.commit()
        engine.tick('production', datetime(2026, 9, 17, 1))
        self.assertEqual(AiDigest.query.count(), 0)

    def test_revision_edit_preserves_live_version_and_withdraws(self):
        content = self.complete()
        engine.publish(content.id, 1, datetime(2026, 9, 17, 1))
        AiDigest.query.one().read_times = 7
        db.session.commit()
        original = AiDigest.query.one().title
        doc = copy.deepcopy(self.doc)
        response = self.client.put('/api/generated-contents/{}/'.format(content.id), headers=ADMIN,
            json={'expected_revision': 1, 'title': '人工修改的标题', 'summary': doc['summary'], 'content': doc['content']})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()['read_times'], 7)
        self.assertEqual(Revision.query.count(), 2)
        self.assertEqual(AiDigest.query.one().title, original)
        stale = self.client.post('/api/generated-contents/{}/'.format(content.id), headers=ADMIN,
                                 json={'expected_revision': 1, 'action': 'publish'})
        self.assertEqual(stale.status_code, 409)
        engine.publish(content.id, 2, datetime(2026, 9, 17, 1, 5))
        self.assertEqual(AiDigest.query.one().title, '人工修改的标题')
        self.assertEqual(AiDigest.query.one().read_times, 7)
        self.client.post('/api/generated-contents/{}/'.format(content.id), headers=ADMIN,
                         json={'expected_revision': 2, 'action': 'withdraw'})
        self.assertEqual(AiDigest.query.one().status, 'withdrawn')

    def test_retry_upload_reuses_text_and_image(self):
        job = self.job()
        current = engine.claim('production', NOW)
        asset = {'sha256': 'f' * 64, 'filename': 'f.png', 'width': 1200, 'height': 600}
        with patch.object(engine.providers, 'readiness', return_value=[]), \
             patch.object(engine.sources, 'collect', return_value=copy.deepcopy(self.inputs)), \
             patch.object(engine.providers, 'generate_text', side_effect=[(self.raw, {}), ({'passed': True, 'issues': []}, {})]) as writer, \
             patch.object(engine.providers, 'generate_image', return_value=(asset, {})) as painter, \
             patch.object(engine.providers, 'upload_image', side_effect=[GenerationError('upload_failed', '暂时失败', True), self.cover]):
            engine.execute(*current)
            self.assertEqual(Job.query.get(job.id).status, 'retry_wait')
            later = NOW + timedelta(minutes=3)
            with patch('app.generation.engine.utcnow', return_value=later):
                engine.execute(*engine.claim('production', later))
            self.assertEqual(Job.query.get(job.id).status, 'succeeded')
            self.assertEqual(writer.call_count, 2)  # one draft + one fact review
            self.assertEqual(painter.call_count, 1)

    def test_config_versions_are_immutable_and_reject_stale_save(self):
        original = Version.query.one().config_json
        payload = {'version': 1, 'enabled': False, 'config': dict(self.config, prompt='新的写作要求')}
        path = '/api/generation/tasks/{}/'.format(self.task.id)
        self.assertEqual(self.client.put(path, json=payload, headers=ADMIN).status_code, 200)
        self.assertEqual(self.client.put(path, json=payload, headers=ADMIN).status_code, 409)
        self.assertEqual(Version.query.filter_by(version=1).one().config_json, original)

    def test_cancel_fences_running_job(self):
        job = self.job()
        current = engine.claim('production', NOW)
        result = self.client.post('/api/generation/jobs/{}/'.format(job.id), json={'action': 'cancel'}, headers=ADMIN)
        self.assertEqual(result.status_code, 200)
        with self.assertRaises(LeaseLost):
            engine.persist_result(*current, self.doc)

    def test_schema_rejects_unknown_source_and_changed_url(self):
        raw = copy.deepcopy(self.raw)
        raw['sections'][0]['source_ids'] = [999]
        with self.assertRaises(GenerationError):
            build_document(raw, self.inputs, self.config, DAY.isoformat())
        doc = copy.deepcopy(self.doc)
        doc['content']['sections'][0]['sources'][0]['url'] = 'https://example.com/invented'
        with self.assertRaises(GenerationError):
            validate_document(doc)

    def test_groups_are_persisted_and_invalid_edits_do_not_create_revisions(self):
        content = self.complete()
        content_id = content.id
        engine.publish(content.id, 1, datetime(2026, 9, 17, 1))
        saved = json.loads(AiDigest.query.one().content_json)
        self.assertEqual(saved['schema_version'], 2)
        self.assertEqual(saved['groups'], self.config['digest_groups'])
        self.assertEqual(saved['sections'][0]['group_id'], 'development')
        response = self.client.get('/api/ai-digests/{}/'.format(content.legacy_digest_id), headers=ADMIN).get_json()
        self.assertEqual(response['content']['schema_version'], 1)
        self.assertEqual(response['content']['groups'], self.config['digest_groups'])
        self.assertEqual([g['id'] for g in response['groups']], ['development'])
        self.assertEqual(response['groups'][0]['count'], 1)
        for invalid in (None, 'unknown'):
            article = copy.deepcopy(saved)
            article['sections'][0]['group_id'] = invalid
            result = self.client.put('/api/generated-contents/{}/'.format(content_id), headers=ADMIN,
                json={'expected_revision': 1, 'title': self.doc['title'], 'summary': self.doc['summary'], 'content': article})
            self.assertEqual(result.status_code, 400)
            self.assertEqual(Revision.query.count(), 1)

    def test_group_validation_legacy_compatibility_and_optional_analysis(self):
        raw = copy.deepcopy(self.raw)
        raw['sections'][0]['analysis'] = ''
        built = build_document(raw, self.inputs, self.config, DAY.isoformat())
        self.assertEqual(built['content']['sections'][0]['analysis'], '')
        del raw['sections'][0]['group_id']
        with self.assertRaises(GenerationError):
            build_document(raw, self.inputs, self.config, DAY.isoformat())
        old = copy.deepcopy(self.doc)
        old['content']['schema_version'] = 1
        del old['content']['groups']
        del old['content']['sections'][0]['group_id']
        validate_document(old)
        old['content']['schema_version'] = 2
        with self.assertRaises(GenerationError):
            validate_document(old)
        config = copy.deepcopy(self.config)
        config['digest_groups'][1]['id'] = 'applications'
        with self.assertRaises(ValueError):
            validate_config(config)

    def test_regeneration_uses_frozen_evidence_and_existing_cover(self):
        content = self.complete()
        engine.publish(content.id, 1, datetime(2026, 9, 17, 1))
        job = engine.enqueue(self.task, DAY, 'regenerate', 'rebuild-1', NOW, reuse_cover=True)
        self.assertEqual(engine.enqueue(self.task, DAY, 'regenerate', 'rebuild-1', NOW, reuse_cover=True).id, job.id)
        with self.assertRaises(ValueError):
            engine.enqueue(self.task, DAY, 'regenerate', 'rebuild-1', NOW)
        current = engine.claim('production', NOW)
        with patch.object(engine.providers, 'readiness', return_value=[]), \
             patch.object(engine.sources, 'collect') as collector, \
             patch.object(engine.providers, 'generate_text', side_effect=[(self.raw, {}), ({'passed': True, 'issues': []}, {})]) as writer, \
             patch.object(engine.providers, 'generate_image') as painter, \
             patch.object(engine.providers, 'upload_image') as uploader:
            engine.execute(*current)
            collector.assert_not_called()
            painter.assert_not_called()
            uploader.assert_not_called()
        self.assertEqual(Job.query.get(job.id).status, 'succeeded')
        self.assertEqual(writer.call_args_list[0][0][2]['sources'][0]['item_id'], self.source['item_id'])
        self.assertEqual(writer.call_args_list[0][0][2]['recent_content'], [])
        self.assertEqual(AiDigest.query.one().content_version, 1)
        revision = Revision.query.filter_by(content_id=content.id, revision=2).one()
        self.assertEqual(json.loads(revision.document_json)['content']['cover']['url'], self.cover['url'])

    def test_date_only_historical_evidence_does_not_invent_timestamps(self):
        inputs = copy.deepcopy(self.inputs)
        inputs['sources'][0]['published_at'] = None
        inputs['sources'][0]['date_precision'] = 'date'
        built = build_document(self.raw, inputs, self.config, DAY.isoformat())
        source = built['content']['sections'][0]['sources'][0]
        self.assertNotIn('published_at', source)
        self.assertEqual(source['date_precision'], 'date')

    def test_new_regeneration_carries_previous_failed_review(self):
        self.complete()
        failed = self.job('test')
        failed.status, failed.error_code = 'failed', 'fact_review_failed'
        failed.checkpoint_json = encode({'review': {'passed': False, 'issues': ['不要将资料缺失写成官方未公布']}})
        db.session.commit()
        job = engine.enqueue(self.task, DAY, 'regenerate', 'correct-review', NOW, reuse_cover=True)
        inputs = json.loads(job.checkpoint_json)['regeneration_inputs']
        self.assertEqual(inputs['feedback_job_id'], failed.id)
        self.assertEqual(inputs['review_feedback'], ['不要将资料缺失写成官方未公布'])

    def test_feed_parser_preserves_publication_time_and_rejects_entities(self):
        from app.generation.sources import parse_feed
        feed = b'<rss><channel><item><title>Update</title><link>https://example.com/news</link><pubDate>Wed, 16 Sep 2026 18:00:00 GMT</pubDate><description>' + b'Verifiable source description. ' * 10 + b'</description></item></channel></rss>'
        result = parse_feed(feed, 'https://example.com/feed')
        self.assertEqual(result[0]['published_at'], datetime(2026, 9, 16, 18))
        west_coast = parse_feed(feed.replace(b'18:00:00 GMT', b'23:30:00 -0700'), 'https://example.com/feed')[0]
        self.assertEqual(west_coast['published_date'], '2026-09-16')
        self.assertEqual(west_coast['published_at'], datetime(2026, 9, 17, 6, 30))
        with self.assertRaises(ValueError):
            parse_feed(b'<!DOCTYPE rss [<!ENTITY x SYSTEM "file:///secret">]><rss/>', 'https://example.com/feed')
        atom = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Old article</title><updated>2026-09-17T00:00:00Z</updated></entry></feed>'
        self.assertEqual(parse_feed(atom, 'https://example.com/feed'), [])

    def test_collector_balances_sources_and_filters_general_news(self):
        from app.generation.sources import collect, title_matches
        self.assertFalse(title_matches('Paid chair sale', ['AI']))
        self.assertTrue(title_matches('豆包工作发布新功能', ['豆包', 'AI']))
        first = AiNewsSource.query.first()
        second = AiNewsSource(name='国内科技媒体', kind='rss', endpoint_url='https://example.cn/feed',
            endpoint_hash=b'd' * 32, enabled=True, priority=1, config_json=encode({'title_keywords': ['豆包']}),
            created_at=NOW, updated_at=NOW)
        db.session.add(second)
        db.session.flush()
        self.config.update(source_ids=[first.id, second.id], max_items=2)
        def feed(titles):
            return ('<rss><channel>' + ''.join('<item><title>{}</title><link>https://example.com/{}</link>'
                '<pubDate>Wed, 16 Sep 2026 20:00:00 GMT</pubDate><description>{}</description></item>'
                .format(title, index + offset, 'Verified source evidence. ' * 5)
                for index, (title, offset) in enumerate(titles)) + '</channel></rss>').encode()
        data = {first.endpoint_url: feed([('Model update', 10), ('Agent update', 20), ('Other update', 30)]),
                second.endpoint_url: feed([('豆包工作发布', 40), ('手机促销', 50)])}
        with patch('app.generation.sources.fetch', side_effect=lambda url: (data[url], {})):
            result = collect(self.config, NOW)
        self.assertEqual({s['source_id'] for s in result['sources']}, {first.id, second.id})
        self.assertIsNone(AiNewsItem.query.filter_by(title='手机促销').first())
        # Later polls may have rolled the story out of RSS; the saved evidence survives.
        with patch('app.generation.sources.fetch', return_value=(b'<rss><channel/></rss>', {})):
            self.assertEqual(len(collect(self.config, NOW)['sources']), 2)

    def test_supplemented_regeneration_freezes_sources_and_checks_cutoff(self):
        content = self.complete()
        item = AiNewsItem(source_id=AiNewsSource.query.first().id, canonical_url='https://example.com/domestic',
            url_hash=b'n' * 32, title='国内发布会', published_at=NOW - timedelta(hours=2), published_date=DAY,
            date_precision='datetime', first_seen_at=NOW, last_seen_at=NOW, content_hash=b'n' * 32)
        item.evidence_json = encode(dict(self.source, url=item.canonical_url, published_at=iso(item.published_at)))
        db.session.add(item)
        db.session.commit()
        job = engine.enqueue(self.task, DAY, 'regenerate', 'supplement', NOW, reuse_cover=True, include_item_ids=[item.id])
        snapshot = json.loads(job.checkpoint_json)['regeneration_inputs']
        self.assertEqual(snapshot['collection_mode'], 'supplemented_evidence')
        self.assertEqual(len(snapshot['sources']), 2)
        with self.assertRaises(ValueError):
            engine.enqueue(self.task, DAY, 'regenerate', 'supplement', NOW, reuse_cover=True)
        item.published_at = NOW + timedelta(hours=1)
        db.session.commit()
        with self.assertRaises(ValueError):
            engine.enqueue(self.task, DAY, 'regenerate', 'future-source', NOW, include_item_ids=[item.id])
        self.assertEqual(len(json.loads(job.checkpoint_json)['regeneration_inputs']['sources']), 2)

    def test_network_rejects_local_targets_and_credentials(self):
        from app.generation.network import validate_url
        for url in ['https://127.0.0.1/x', 'https://[::1]/x', 'http://example.com/x', 'https://user:password@example.com/x', 'https://localhost/x', 'https://169.254.169.254/']:
            with self.assertRaises(ValueError, msg=url):
                validate_url(url, resolve=False)

    def test_manual_request_is_idempotent(self):
        first = self.job('test')
        second = self.job('test')
        self.assertEqual(first.id, second.id)
        self.assertEqual(Job.query.count(), 1)

    def test_missing_model_configuration_fails_without_publication(self):
        self.job()
        current = engine.claim('production', NOW)
        engine.execute(*current)
        self.assertEqual(Job.query.one().status, 'failed')
        self.assertEqual(Job.query.one().error_code, 'configuration_missing')
        self.assertEqual(Content.query.count(), 0)

    def test_failed_fact_review_keeps_evidence_and_never_publishes(self):
        self.job()
        current = engine.claim('production', NOW)
        with patch.object(engine.providers, 'readiness', return_value=[]), \
             patch.object(engine.sources, 'collect', return_value=copy.deepcopy(self.inputs)), \
             patch.object(engine.providers, 'generate_text', side_effect=[(self.raw, {}), ({'passed': False, 'issues': ['原文不支持该数字']}, {})]), \
             patch.object(engine.providers, 'generate_image', return_value=({'sha256': 'f' * 64}, {})), \
             patch.object(engine.providers, 'upload_image', return_value=self.cover):
            engine.execute(*current)
        job = Job.query.one()
        self.assertEqual(job.error_code, 'fact_review_failed')
        self.assertEqual(json.loads(job.checkpoint_json)['review']['issues'], ['原文不支持该数字'])
        self.assertEqual(Content.query.count(), 0)

    def test_historical_run_does_not_collect_todays_news(self):
        edition = DAY - timedelta(days=1)
        engine.enqueue(self.task, edition, 'test', 'backfill-request-001', NOW)
        current = engine.claim('production', NOW)
        with patch.object(engine.providers, 'readiness', return_value=[]), \
             patch.object(engine.sources, 'collect', side_effect=GenerationError('stop', '止于采集器')) as collector:
            engine.execute(*current)
        self.assertEqual(collector.call_args[0][1], datetime(2026, 9, 16, 1))

    def test_development_never_auto_publishes_even_with_ready_content(self):
        content = self.complete()
        content.environment = 'development'
        db.session.commit()
        engine.tick('development', datetime(2026, 9, 17, 1))
        self.assertEqual(AiDigest.query.count(), 0)

    def test_deadline_prevents_catchup_and_automatic_publication(self):
        content = self.complete()
        engine.tick('production', datetime(2026, 9, 17, 5))
        self.assertEqual(AiDigest.query.count(), 0)
        self.assertEqual(Job.query.count(), 1)

    def test_cli_bootstrap_worker_scheduler_and_production_guard(self):
        with TemporaryDirectory() as directory:
            url = 'sqlite:///' + str(Path(directory) / 'generation.sqlite')
            isolated = create_engine(url)
            with isolated.begin() as connection:
                with Operations.context(MigrationContext.configure(connection)):
                    migration('20260916_ai_digest').upgrade()
                    migration('20260916_generation').upgrade()
                    migration('20260916_digest_reads').upgrade()
            environment = dict(os.environ, TEST_DATABASE_URL=url, FLASK_CONFIG='testing',
                CONTENT_ENVIRONMENT='development', CONTENT_JOBS_ENABLED='1')
            root = str(Path(__file__).parents[1])
            for arguments in (['init'], ['init'], ['scheduler', '--environment', 'development', '--once'],
                              ['worker', '--environment', 'development', '--once']):
                result = subprocess.run([sys.executable, 'generation.py'] + arguments,
                    cwd=root, env=environment, capture_output=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
            environment['CONTENT_ENVIRONMENT'] = 'production'
            blocked = subprocess.run([sys.executable, 'generation.py', 'worker', '--environment', 'production', '--once'],
                cwd=root, env=environment, capture_output=True, timeout=15)
            self.assertEqual(blocked.returncode, 2)
            with isolated.connect() as connection:
                self.assertEqual(connection.execute(sql_text('select count(*) from generation_tasks')).scalar(), 1)
                self.assertEqual(connection.execute(sql_text('select count(*) from generation_task_versions')).scalar(), 1)
                self.assertEqual(connection.execute(sql_text('select count(*) from generation_jobs')).scalar(), 0)
            # CLI trigger only queues work. No fake content or external requests are produced.
            config = default_config()
            config['source_ids'] = [1]
            for name in ('text_model', 'image_model'):
                config[name].update(base_url='https://example.com/v1', model='test-model')
            with isolated.begin() as connection:
                connection.execute(sql_text("update generation_tasks set environment='development'"))
                connection.execute(sql_text('update generation_task_versions set config_json=:config'), config=encode(config))
            environment.update(CONTENT_ENVIRONMENT='development', CONTENT_TEXT_API_KEY='unit-test-key',
                CONTENT_IMAGE_API_KEY='unit-test-key', QI_NIU_ACCESS_KEY='unit-test-key', QI_NIU_SECRET_KEY='unit-test-key',
                DEV_QI_NIU_BUCKET='unit-test', DEV_QI_NIU_LINK_URL='https://example.com')
            def command(*arguments):
                return subprocess.run([sys.executable, 'generation.py'] + list(arguments) + ['--environment', 'development'],
                    cwd=root, env=environment, capture_output=True, timeout=15)
            first = command('run', '--task-id', '1', '--request-id', 'ops-test-1')
            self.assertEqual(first.returncode, 0, first.stderr.decode())
            job_id = json.loads(first.stdout)['job_id']
            again = command('run', '--task-id', '1', '--request-id', 'ops-test-1')
            self.assertEqual(json.loads(again.stdout)['job_id'], job_id)
            status = command('status', '--job-id', str(job_id))
            self.assertEqual(json.loads(status.stdout)['status'], 'queued')
            with isolated.begin() as connection:
                connection.execute(sql_text("update generation_jobs set status='failed' where id=:id"), id=job_id)
            retried = command('retry', '--job-id', str(job_id))
            self.assertEqual(retried.returncode, 0, retried.stderr.decode())
            self.assertEqual(json.loads(retried.stdout)['status'], 'queued')
            self.assertEqual(command('run', '--task-id', '1', '--date', '2999-01-01').returncode, 2)
            self.assertEqual(command('run', '--task-id', '1', '--mode', 'scheduled').returncode, 2)
            self.assertEqual(command('run', '--task-id', '1', '--reuse-cover').returncode, 2)
            self.assertEqual(command('run', '--task-id', '1', '--mode', 'regenerate', '--reuse-cover').returncode, 2)
            with isolated.connect() as connection:
                self.assertEqual(connection.execute(sql_text('select count(*) from generation_jobs')).scalar(), 1)
                self.assertEqual(connection.execute(sql_text('select count(*) from generated_contents')).scalar(), 0)
            isolated.dispose()


if __name__ == '__main__':
    unittest.main()
