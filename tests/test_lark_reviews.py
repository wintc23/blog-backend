"""Moderation cards use genuine API auth, version guards and durable decisions."""
import json
import os
import time
import unittest
from unittest.mock import patch

import test_lark_bridge as fixtures
from app import db
from app.models import Comment, Message, StatEvent, Role
from lark_bridge.reviews import card


class ReviewTests(unittest.TestCase):
    def setUp(self):
        fixtures.BridgeTests.setUp(self)
        for table in (Comment.__table__, Message.__table__, StatEvent.__table__):
            table.create(db.engine)
        db.session.add(Comment(id=1, body='<p>请审核这条评论</p>', author_id=1, hide=True))
        db.session.add(Message(id=1, body='<p>请审核这条留言</p>', author_id=1, hide=True))
        db.session.commit()
        self.reviews = self.bridge.reviews
        self.lark.send_card.return_value = {'message_id': 'om_card', 'chat_id': 'oc_owner'}

    def tearDown(self):
        fixtures.BridgeTests.tearDown(self)

    def prepare_card(self, kind='comment', target=1):
        result = self.reviews.request(kind, target)
        with self.store.connect() as c:
            outbox = dict(c.execute('SELECT * FROM outbox WHERE content=?', (result['review_id'],)).fetchone())
        self.reviews.send(outbox)
        self.store.reply_result(outbox, True)
        return self.reviews.get(result['review_id'])

    def callback(self, row, decision='approve', **changes):
        return dict({'type': 'card.action.trigger', 'action_tag': 'button', 'operator_id': fixtures.OWNER,
                     'message_id': row['card_message_id'], 'chat_id': row['chat_id'], 'event_id': 'callback-1',
                     'action_value': json.dumps({'review_id': row['id'], 'nonce': row['nonce'], 'decision': decision})}, **changes)

    def test_scan_sends_one_card_per_pending_revision_including_messages(self):
        self.reviews.scan()
        self.reviews.scan()
        with self.store.connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM reviews').fetchone()[0], 2)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM outbox WHERE kind='review_card'").fetchone()[0], 2)

    def test_owner_approval_uses_existing_api_and_updates_card(self):
        row = self.prepare_card()
        self.assertTrue(self.reviews.handle(self.callback(row)))
        self.assertTrue(self.reviews.work_one())
        self.assertFalse(Comment.query.get(1).hide)
        self.assertEqual(StatEvent.query.filter_by(name='comment.approved').count(), 1)
        done = self.reviews.get(row['id'])
        self.assertEqual(done['state'], 'approved')
        with patch.dict(os.environ, {'LARK_BRIDGE_SITE_URL': ''}):
            self.assertFalse(any(x['tag'] == 'action' for x in card(done)['elements']))

    def test_reject_keeps_content_hidden_without_deleting_or_renotifying(self):
        row = self.prepare_card('message')
        self.assertTrue(self.reviews.handle(self.callback(row, 'reject')))
        self.reviews.work_one()
        self.assertTrue(Message.query.get(1).hide)
        self.assertEqual(self.reviews.get(row['id'])['state'], 'rejected')
        self.assertEqual(self.reviews.request('message', 1)['review_id'], row['id'])
        self.assertEqual(StatEvent.query.count(), 0)

    def test_forged_operator_nonce_card_and_conversation_rejected(self):
        row = self.prepare_card()
        forged_value = json.dumps({'review_id': row['id'], 'nonce': 'wrong', 'decision': 'approve'})
        for changes in ({'operator_id': 'ou_other'}, {'message_id': 'om_other'}, {'chat_id': 'oc_other'},
                        {'action_value': forged_value}, {'action_value': '[]'}, {'action_tag': 'input'}):
            self.assertFalse(self.reviews.handle(self.callback(row, **changes)))
        self.assertFalse(self.reviews.work_one())
        self.assertTrue(Comment.query.get(1).hide)

    def test_double_click_and_opposite_decision_execute_once(self):
        row = self.prepare_card()
        self.assertTrue(self.reviews.handle(self.callback(row)))
        self.assertFalse(self.reviews.handle(self.callback(row)))
        self.assertFalse(self.reviews.handle(self.callback(row, 'reject', event_id='other')))
        self.reviews.work_one()
        self.assertFalse(self.reviews.work_one())
        self.assertEqual(StatEvent.query.count(), 1)

    def test_changed_content_after_click_is_not_approved(self):
        row = self.prepare_card()
        self.reviews.handle(self.callback(row))
        Comment.query.get(1).body = '<p>已替换的内容</p>'
        db.session.commit()
        self.reviews.work_one()
        self.assertTrue(Comment.query.get(1).hide)
        self.assertEqual(self.reviews.get(row['id'])['state'], 'stale')
        self.assertEqual(StatEvent.query.count(), 0)
        new = self.reviews.request('comment', 1)
        self.assertNotEqual(new['review_id'], row['id'])

    def test_external_approval_marks_old_card_stale(self):
        row = self.prepare_card()
        Comment.query.get(1).hide = False
        db.session.commit()
        self.reviews.scan()
        self.assertEqual(self.reviews.get(row['id'])['state'], 'stale')

    def test_previously_approved_content_hidden_again_gets_new_card(self):
        row = self.prepare_card()
        self.reviews.handle(self.callback(row))
        self.reviews.work_one()
        Comment.query.get(1).hide = True
        db.session.commit()
        self.assertNotEqual(self.reviews.request('comment', 1)['review_id'], row['id'])

    def test_expired_card_cannot_execute_and_is_reissued(self):
        row = self.prepare_card()
        with self.store.connect() as c:
            c.execute('UPDATE reviews SET expires=? WHERE id=?', (time.time() - 1, row['id']))
        self.assertFalse(self.reviews.handle(self.callback(row)))
        self.assertEqual(self.reviews.get(row['id'])['state'], 'expired')
        self.assertNotEqual(self.reviews.request('comment', 1)['review_id'], row['id'])

    def test_revoked_admin_is_rechecked_after_click(self):
        row = self.prepare_card()
        self.reviews.handle(self.callback(row))
        Role.query.first().permissions = 2
        db.session.commit()
        self.reviews.work_one()
        self.assertEqual(self.reviews.get(row['id'])['state'], 'failed')
        self.assertTrue(Comment.query.get(1).hide)

    def test_recovery_never_replays_uncertain_approval(self):
        row = self.prepare_card()
        with self.store.connect() as c:
            c.execute("UPDATE reviews SET state='applying',decision='approve' WHERE id=?", (row['id'],))
        self.reviews.recover()
        self.assertEqual(self.reviews.get(row['id'])['state'], 'interrupted')
        self.assertFalse(self.reviews.work_one())

    def test_planner_cannot_bypass_card_through_generic_api(self):
        with self.assertRaises(ValueError):
            self.bridge.site.request({'path': '/api/set-comment-show/1'})
        result = self.bridge.execute({'id': 1}, 'site.request', {'path': '/api/set-comment-show/1'}, 0)
        self.assertEqual(result['status'], 'pending')
        self.assertTrue(Comment.query.get(1).hide)

    def test_card_plain_text_and_configured_domain(self):
        row = self.prepare_card()
        with patch.dict(os.environ, {'LARK_BRIDGE_SITE_URL': 'https://site.example/sub'}):
            value = card(row)
        buttons = value['elements'][-1]['actions']
        self.assertEqual(buttons[-1]['url'], 'https://site.example/sub/manage/comment')
        self.assertEqual(value['elements'][1]['text']['tag'], 'plain_text')
        self.assertNotIn('<p>', value['elements'][1]['text']['content'])

    def test_pending_decision_not_executed_under_another_app(self):
        row = self.prepare_card()
        self.reviews.handle(self.callback(row))
        self.reviews.app_id = 'cli_other'
        self.assertFalse(self.reviews.work_one())
        self.assertTrue(Comment.query.get(1).hide)

    def test_transport_sends_owner_card_and_patches_original(self):
        from lark_bridge.lark import Lark
        lark = Lark('lark-cli', self.temp.name, 'cli_test')
        row = self.prepare_card()
        rendered = card(row)
        with patch.object(lark, 'run', return_value={'data': {'message_id': 'om_card', 'chat_id': 'oc_owner'}}) as run:
            lark.send_card(fixtures.OWNER, rendered, 'stable-id')
            arguments = run.call_args[0][0]
            payload = json.loads(arguments[arguments.index('--data') + 1])
            self.assertEqual(payload['receive_id'], fixtures.OWNER)
            self.assertEqual(payload['msg_type'], 'interactive')
            self.assertEqual(json.loads(payload['content']), rendered)
            lark.update_card('om_card', rendered)
            self.assertEqual(run.call_args[0][0][:3], ['im', 'messages', 'patch'])

    def test_failed_card_send_keeps_same_approval_and_idempotency_key(self):
        result = self.reviews.request('comment', 1)
        outbox = self.store.pending_reply()
        with patch.object(self.lark, 'send_card', side_effect=RuntimeError('network')):
            with self.assertRaises(RuntimeError):
                self.reviews.send(outbox)
        self.assertIsNone(self.reviews.get(result['review_id'])['card_message_id'])
        self.reviews.send(outbox)
        self.assertEqual(self.lark.send_card.call_args[0][2], outbox['dedup'])


if __name__ == '__main__':
    unittest.main()
