"""Corrective generation uses isolated SQLite and mock model/storage calls."""
import copy
import json
import unittest
from contextlib import ExitStack
from datetime import timedelta
from unittest.mock import patch
import test_generation as fixture
from app import db
from app.digest_models import AiDigest
from app.generation import engine
from app.generation_models import GenerationJob as Job, GeneratedContent as Content, GenerationTaskVersion as Version
from app.generation.configuration import encode


class ReviewRetryTests(unittest.TestCase):
    setUp = fixture.GenerationTests.setUp
    tearDown = fixture.GenerationTests.tearDown
    job = fixture.GenerationTests.job

    def mocks(self, stack, responses):
        stack.enter_context(patch.object(engine.providers, 'readiness', return_value=[]))
        collect = stack.enter_context(patch.object(engine.sources, 'collect', return_value=copy.deepcopy(self.inputs)))
        writer = stack.enter_context(patch.object(engine.providers, 'generate_text', side_effect=responses))
        painter = stack.enter_context(patch.object(engine.providers, 'generate_image', return_value=({'sha256': 'f' * 64}, {})))
        uploader = stack.enter_context(patch.object(engine.providers, 'upload_image', return_value=self.cover))
        return collect, writer, painter, uploader

    def test_rejected_facts_are_rewritten_rechecked_then_published_with_one_image(self):
        job_id = self.job().id
        rejected = {'passed': False, 'issues': ['标题误将两家公司的工具混为一谈']}
        corrected = dict(self.raw, title='经过修正的新闻标题')
        with ExitStack() as stack:
            collect, writer, painter, upload = self.mocks(stack, [
                (self.raw, {}), (rejected, {}), (corrected, {}), ({'passed': True, 'issues': []}, {})])
            engine.execute(*engine.claim('production', fixture.NOW))
            self.assertEqual(Job.query.get(job_id).status, 'retry_wait')
            self.assertEqual(Content.query.count(), 0)
            later = fixture.NOW + timedelta(minutes=3)
            with patch.object(engine, 'utcnow', return_value=later):
                engine.execute(*engine.claim('production', later))
            job = Job.query.get(job_id)
            self.assertEqual(job.status, 'succeeded')
            self.assertEqual(job.attempt, 2)
            self.assertEqual(writer.call_count, 4)
            correction_input = writer.call_args_list[2].args[2]
            self.assertEqual(correction_input['review_feedback'], rejected['issues'])
            self.assertEqual(correction_input['sources'], self.inputs['sources'])
            self.assertEqual(correction_input['previous_document']['title'], self.raw['title'])
            self.assertEqual(writer.call_args_list[3].args[2]['title'], corrected['title'])
            self.assertEqual([collect.call_count, painter.call_count, upload.call_count], [1, 1, 1])
            checkpoint = json.loads(job.checkpoint_json)
            self.assertEqual(checkpoint['review_history'][0]['review'], rejected)
            self.assertEqual(checkpoint['review_history'][0]['attempt'], 1)
            self.assertEqual(Content.query.one().status, 'ready')
            self.assertEqual(AiDigest.query.count(), 0)
            engine.tick('production', fixture.NOW + timedelta(minutes=31))
            self.assertEqual(AiDigest.query.one().title, corrected['title'])
            self.assertEqual(Content.query.one().status, 'published')

    def test_failed_review_retries_stop_at_the_configured_budget(self):
        self.config['max_retries'] = 1
        Version.query.one().config_json = encode(self.config)
        db.session.commit()
        self.job()
        with ExitStack() as stack:
            self.mocks(stack, [(self.raw, {}), ({'passed': False, 'issues': ['事实不符']}, {})] * 2)
            engine.execute(*engine.claim('production', fixture.NOW))
            later = fixture.NOW + timedelta(minutes=3)
            with patch.object(engine, 'utcnow', return_value=later):
                engine.execute(*engine.claim('production', later))
        self.assertEqual(Job.query.one().status, 'failed')
        self.assertEqual(Job.query.one().attempt, 2)
        self.assertEqual(Job.query.one().error_code, 'fact_review_failed')
        self.assertIsNone(engine.claim('production', fixture.NOW + timedelta(minutes=30)))
        self.assertEqual(Content.query.count(), 0)

    def test_manual_retry_recovers_an_old_failed_review_instead_of_reusing_it(self):
        job = self.job()
        job.status, job.stage, job.attempt = 'failed', 'validate', 1
        job.error_code = 'fact_review_failed'
        job.checkpoint_json = encode({'inputs': self.inputs, 'document': self.doc,
            'asset': {'sha256': 'f' * 64}, 'uploaded_cover': self.cover,
            'review': {'passed': False, 'issues': ['修正标题']}})
        db.session.commit()
        engine.control_job(job.id, 'retry', 'production')
        with ExitStack() as stack:
            collect, writer, painter, upload = self.mocks(stack, [(self.raw, {}), ({'passed': True, 'issues': []}, {})])
            engine.execute(*engine.claim('production', fixture.NOW))
        self.assertEqual(Job.query.one().status, 'succeeded')
        self.assertEqual(writer.call_count, 2)
        self.assertEqual([collect.call_count, painter.call_count, upload.call_count], [0, 0, 0])
        self.assertEqual(writer.call_args_list[0].args[2]['review_feedback'], ['修正标题'])

    def test_review_failure_at_deadline_does_not_schedule_another_attempt(self):
        job = self.job()
        job.deadline_at = fixture.NOW + timedelta(seconds=30)
        db.session.commit()
        with ExitStack() as stack:
            self.mocks(stack, [(self.raw, {}), ({'passed': False, 'issues': ['事实不符']}, {})])
            engine.execute(*engine.claim('production', fixture.NOW))
        self.assertEqual(Job.query.one().status, 'failed')
        self.assertEqual(Content.query.count(), 0)

    def test_cancelled_retry_cannot_generate_or_publish(self):
        job = self.job()
        with ExitStack() as stack:
            self.mocks(stack, [(self.raw, {}), ({'passed': False, 'issues': ['事实不符']}, {})])
            engine.execute(*engine.claim('production', fixture.NOW))
        engine.control_job(job.id, 'cancel', 'production')
        self.assertIsNone(engine.claim('production', fixture.NOW + timedelta(minutes=5)))
        self.assertEqual(Content.query.count(), 0)
