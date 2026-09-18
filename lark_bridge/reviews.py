"""Persistent moderation cards. Callbacks select stored actions, never API paths."""
import hashlib
import hmac
import json
import secrets
import time
import uuid
from html.parser import HTMLParser

from .configuration import site_url
from .store import encode


LABELS = {'comment': '评论', 'message': '留言'}
STATES = {'pending': '待审核', 'queued': '处理中', 'applying': '处理中', 'approved': '已通过',
          'rejected': '已拒绝（保持隐藏）', 'stale': '内容已改变或已处理，请查看新卡片',
          'expired': '卡片已过期', 'failed': '执行失败，请在后台核实', 'interrupted': '执行中断，请在后台核实'}


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag == 'img':
            self.parts.append('[图片：请查看原文]')
        elif tag in ('br', 'p', 'div', 'li'):
            self.parts.append('\n')


def snapshot(kind, row):
    return {'kind': kind, 'id': row.id, 'body': row.body or '', 'author_id': row.author_id,
            'response_id': row.response_id, 'timestamp': row.timestamp.isoformat() if row.timestamp else '',
            'post_id': getattr(row, 'post_id', None), 'digest_id': getattr(row, 'digest_id', None),
            'root_response_id': getattr(row, 'root_response_id', None)}


def revision(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def card(review):
    data = json.loads(review['snapshot'])
    text = PlainText()
    text.feed(data['body'])
    preview = ''.join(text.parts).strip() or '（无文字内容）'
    elements = [{'tag': 'div', 'text': {'tag': 'plain_text', 'content':
                 '作者 ID：{}\n提交时间：{}\n状态：{}'.format(data['author_id'], data['timestamp'], STATES[review['state']])}},
                {'tag': 'div', 'text': {'tag': 'plain_text', 'content': preview[:4000]}}]
    if len(preview) > 4000:
        elements.append({'tag': 'div', 'text': {'tag': 'plain_text', 'content': '内容较长，以上仅为摘要；请查看原文后审核。'}})
    base = site_url()
    buttons = []
    if review['state'] == 'pending':
        for decision, title, style in (('approve', '通过并公开', 'primary'), ('reject', '拒绝并保持隐藏', 'danger')):
            buttons.append({'tag': 'button', 'text': {'tag': 'plain_text', 'content': title}, 'type': style,
                            'value': {'review_id': review['id'], 'nonce': review['nonce'], 'decision': decision}})
    if base:
        path = '/manage/comment' if review['kind'] == 'comment' else '/manage/message'
        buttons.append({'tag': 'button', 'text': {'tag': 'plain_text', 'content': '查看网站后台'},
                        'type': 'default', 'url': base + path})
    if buttons:
        elements.append({'tag': 'action', 'actions': buttons})
    return {'config': {'wide_screen_mode': True, 'update_multi': True},
            'header': {'template': 'blue' if review['state'] == 'pending' else 'grey',
                       'title': {'tag': 'plain_text', 'content': '{}审核 #{}'.format(LABELS[review['kind']], review['target_id'])}},
            'elements': elements}


class Reviews:
    def __init__(self, store, site, lark, owner, app_id):
        self.store, self.site, self.lark, self.owner, self.app_id = store, site, lark, owner, app_id
        with store.connect() as c:
            c.executescript('''
                CREATE TABLE IF NOT EXISTS reviews (
                  id TEXT PRIMARY KEY, kind TEXT NOT NULL, target_id INTEGER NOT NULL,
                  revision TEXT NOT NULL, snapshot TEXT NOT NULL, nonce TEXT NOT NULL,
                  owner TEXT NOT NULL, app_id TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
                  card_message_id TEXT, chat_id TEXT, decision TEXT, decided_by TEXT,
                  created REAL NOT NULL, expires REAL NOT NULL, decided REAL, result TEXT);
                CREATE INDEX IF NOT EXISTS review_target ON reviews(kind,target_id,created);
                CREATE TABLE IF NOT EXISTS review_callbacks(event_id TEXT PRIMARY KEY, review_id TEXT NOT NULL);
            ''')

    @staticmethod
    def model(kind):
        from app.models import Comment, Message
        return {'comment': Comment, 'message': Message}[kind]

    def get(self, review_id):
        with self.store.connect() as c:
            row = c.execute('SELECT * FROM reviews WHERE id=?', (review_id,)).fetchone()
            return dict(row) if row else None

    def update(self, c, row, state, result=None):
        c.execute('UPDATE reviews SET state=?,result=? WHERE id=?', (state, encode(result), row['id']))
        if row['card_message_id']:
            self.store._outbox(c, row['card_message_id'], row['id'], 'review-update:{}:{}'.format(row['id'], state), 'review_update')

    def recover(self):
        with self.store.connect() as c:
            for row in c.execute("SELECT * FROM reviews WHERE state='applying'").fetchall():
                self.update(c, row, 'interrupted')

    def request(self, kind, target_id):
        from app import db
        self.site.admin_token()
        with self.site.app.app_context():
            try:
                row = self.model(kind).query.get(target_id)
                if not row or not row.hide:
                    return {'status': 'not_pending'}
                value = snapshot(kind, row)
            finally:
                db.session.remove()
        digest, now = revision(value), time.time()
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            rows = c.execute('SELECT * FROM reviews WHERE kind=? AND target_id=? AND app_id=? AND owner=? ORDER BY created DESC',
                             (kind, target_id, self.app_id, self.owner)).fetchall()
            for old in rows:
                if old['revision'] == digest and old['state'] not in ('expired', 'stale', 'approved'):
                    if old['state'] != 'pending' or old['expires'] > now:
                        return {'status': old['state'], 'review_id': old['id']}
                if old['state'] in ('pending', 'queued'):
                    self.update(c, old, 'expired' if old['revision'] == digest else 'stale')
            review_id = uuid.uuid4().hex
            c.execute('INSERT INTO reviews(id,kind,target_id,revision,snapshot,nonce,owner,app_id,created,expires) VALUES(?,?,?,?,?,?,?,?,?,?)',
                      (review_id, kind, target_id, digest, encode(value), secrets.token_urlsafe(24), self.owner, self.app_id, now, now + 7 * 86400))
            self.store._outbox(c, self.owner, review_id, 'review-send:' + review_id, 'review_card')
            return {'status': 'pending', 'review_id': review_id, 'message': '审核卡片已排队发送给 Owner'}

    def scan(self):
        from app import db
        self.site.admin_token()
        with self.store.connect() as c:
            active = [dict(r) for r in c.execute("SELECT * FROM reviews WHERE state IN ('pending','queued') AND owner=? AND app_id=?",
                                               (self.owner, self.app_id))]
        for review in active:
            with self.site.app.app_context():
                try:
                    row = self.model(review['kind']).query.get(review['target_id'])
                    stale = not row or not row.hide or revision(snapshot(review['kind'], row)) != review['revision']
                finally:
                    db.session.remove()
            if stale:
                with self.store.connect() as c:
                    current = c.execute('SELECT * FROM reviews WHERE id=?', (review['id'],)).fetchone()
                    if current['state'] in ('pending', 'queued'):
                        self.update(c, current, 'stale')
        # Iterate IDs in batches; includes replies and pre-existing pending items.
        for kind in LABELS:
            last = 0
            while True:
                with self.site.app.app_context():
                    try:
                        ids = [r.id for r in self.model(kind).query.filter_by(hide=True).filter(self.model(kind).id > last).order_by(self.model(kind).id).limit(100)]
                    finally:
                        db.session.remove()
                if not ids:
                    break
                for target_id in ids:
                    self.request(kind, target_id)
                last = ids[-1]

    def send(self, outbox):
        row = self.get(outbox['content'])
        if not row or row['owner'] != self.owner or row['app_id'] != self.app_id:
            return
        if outbox['kind'] == 'review_card':
            if row['card_message_id']:
                return
            if row['state'] != 'pending':
                return
            response = self.lark.send_card(self.owner, card(row), outbox['dedup'])
            with self.store.connect() as c:
                c.execute('UPDATE reviews SET card_message_id=?,chat_id=? WHERE id=?',
                          (response['message_id'], response['chat_id'], row['id']))
        elif row['card_message_id']:
            self.lark.update_card(row['card_message_id'], card(row))

    def handle(self, event):
        if (event.get('type') != 'card.action.trigger' or event.get('operator_id') != self.owner
                or event.get('action_tag') != 'button' or not event.get('event_id')):
            return False
        try:
            value = json.loads(event.get('action_value', ''))
            if not isinstance(value, dict) or value.get('decision') not in ('approve', 'reject'):
                return False
            with self.store.connect() as c:
                c.execute('BEGIN IMMEDIATE')
                row = c.execute('SELECT * FROM reviews WHERE id=?', (value.get('review_id'),)).fetchone()
                if (not row or row['state'] != 'pending' or row['owner'] != self.owner or row['app_id'] != self.app_id
                        or not isinstance(value.get('nonce'), str) or not hmac.compare_digest(row['nonce'], value['nonce'])
                        or not row['card_message_id'] or row['card_message_id'] != event.get('message_id')
                        or row['chat_id'] != event.get('chat_id')):
                    return False
                if row['expires'] <= time.time():
                    self.update(c, row, 'expired')
                    return False
                if c.execute('SELECT 1 FROM review_callbacks WHERE event_id=?', (event['event_id'],)).fetchone():
                    return False
                c.execute('INSERT INTO review_callbacks VALUES(?,?)', (event['event_id'], row['id']))
                c.execute('UPDATE reviews SET decision=?,decided_by=?,decided=? WHERE id=?',
                          (value['decision'], self.owner, time.time(), row['id']))
                self.update(c, row, 'queued')
            return True
        except (ValueError, TypeError):
            return False

    def guard(self, review):
        from flask import jsonify
        model = self.model(review['kind'])
        row = model.query.filter_by(id=review['target_id']).with_for_update().first()
        if not row or not row.hide or revision(snapshot(review['kind'], row)) != review['revision']:
            return jsonify({'error': 'review_stale'}), 409

    def work_one(self):
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute("SELECT * FROM reviews WHERE state='queued' AND owner=? AND app_id=? ORDER BY decided LIMIT 1",
                            (self.owner, self.app_id)).fetchone()
            if not row:
                return False
            row = dict(row)
            c.execute("UPDATE reviews SET state='applying' WHERE id=?", (row['id'],))
        try:
            self.site.admin_token()
            if row['decision'] == 'approve':
                result = self.site.request({'method': 'GET', 'path': '/api/set-{}-show/{}'.format(row['kind'], row['target_id'])},
                                           guard=lambda: self.guard(row))
                state = 'approved' if result['status'] == 200 else 'stale' if result['status'] == 409 else 'failed'
            else:
                from app import db
                with self.site.app.test_request_context():
                    try:
                        stale = self.guard(row)
                        result, state = {'kept_hidden': not bool(stale)}, 'stale' if stale else 'rejected'
                    finally:
                        db.session.rollback()
                        db.session.remove()
        except Exception:
            result, state = {'error': '执行失败，需人工核实；不会自动重试'}, 'failed'
        with self.store.connect() as c:
            self.update(c, row, state, result)
        return True
