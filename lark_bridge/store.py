"""Durable inbox, execution journal and outbox. Never replay an uncertain write."""
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


class Store:
    def __init__(self, directory):
        self.root = Path(directory).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(str(self.root), 0o700)
        self.path = self.root / 'bridge.sqlite3'
        with self.connect() as c:
            c.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY, message_id TEXT UNIQUE NOT NULL,
                    chat_id TEXT NOT NULL, session TEXT NOT NULL, event TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'queued', cancelled INTEGER NOT NULL DEFAULT 0,
                    result TEXT, created REAL NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS actions (
                    id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL, tool TEXT NOT NULL,
                    args TEXT NOT NULL, result TEXT, state TEXT NOT NULL DEFAULT 'running');
                CREATE TABLE IF NOT EXISTS outbox (
                    id INTEGER PRIMARY KEY, message_id TEXT NOT NULL, content TEXT NOT NULL,
                    kind TEXT NOT NULL DEFAULT 'text', dedup TEXT UNIQUE NOT NULL,
                    sent INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt REAL NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS assets (
                    id TEXT PRIMARY KEY, session TEXT NOT NULL, path TEXT NOT NULL,
                    url TEXT, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            ''')
            c.execute('INSERT OR IGNORE INTO meta VALUES (?, ?)', ('started', str(time.time())))
        os.chmod(str(self.path), 0o600)

    @contextmanager
    def connect(self):
        c = sqlite3.connect(str(self.path), timeout=30)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    def enqueue(self, event, state='queued'):
        now = time.time()
        session = event['chat_id'] + ':' + (event.get('thread_id') or 'main')
        with self.connect() as c:
            row = c.execute('SELECT value FROM meta WHERE key=?', ('epoch:' + session,)).fetchone()
            session += ':' + (row['value'] if row else '0')
            c.execute('INSERT OR IGNORE INTO jobs(message_id,chat_id,session,event,created,updated,state) VALUES(?,?,?,?,?,?,?)',
                      (event['message_id'], event['chat_id'], session, encode(event), now, now, state))
            if c.execute('SELECT changes()').fetchone()[0] == 0:
                return None
            job_id = c.execute('SELECT last_insert_rowid()').fetchone()[0]
            return job_id

    def claim(self):
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY id LIMIT 1").fetchone()
            if row:
                c.execute("UPDATE jobs SET state='running', updated=? WHERE id=?", (time.time(), row['id']))
                return dict(row)

    def finish(self, job, state, result):
        with self.connect() as c:
            c.execute('UPDATE jobs SET state=?,result=?,updated=? WHERE id=?',
                      (state, result, time.time(), job['id']))
            self._outbox(c, job['message_id'], result, 'result:' + str(job['id']))

    def recover(self):
        with self.connect() as c:
            rows = c.execute("SELECT * FROM jobs WHERE state='running'").fetchall()
        for row in rows:
            self.finish(dict(row), 'interrupted', '任务 #{} 因服务重启中断。已完成的操作不会自动重做，请先查询结果再继续。'.format(row['id']))

    @staticmethod
    def _outbox(c, message_id, content, dedup, kind='text'):
        c.execute('INSERT OR IGNORE INTO outbox(message_id,content,dedup,kind) VALUES(?,?,?,?)',
                  (message_id, content, dedup, kind))

    def reply(self, message_id, content, dedup, kind='text'):
        with self.connect() as c:
            self._outbox(c, message_id, content, dedup, kind)

    def pending_reply(self):
        with self.connect() as c:
            row = c.execute('SELECT * FROM outbox WHERE sent=0 AND next_attempt<=? ORDER BY id LIMIT 1', (time.time(),)).fetchone()
            return dict(row) if row else None

    def reply_result(self, row, success):
        with self.connect() as c:
            c.execute('UPDATE outbox SET sent=?,attempts=attempts+1,next_attempt=? WHERE id=?',
                      (int(success), time.time() + min(300, 2 ** min(row['attempts'] + 1, 8)), row['id']))

    def action(self, job_id, tool, args):
        with self.connect() as c:
            return c.execute('INSERT INTO actions(job_id,tool,args) VALUES(?,?,?)', (job_id, tool, encode(args))).lastrowid

    def action_result(self, action_id, result):
        with self.connect() as c:
            c.execute("UPDATE actions SET state='done',result=? WHERE id=?", (encode(result), action_id))

    def cancelled(self, job_id):
        with self.connect() as c:
            return bool(c.execute('SELECT cancelled FROM jobs WHERE id=?', (job_id,)).fetchone()[0])

    def history(self, job):
        with self.connect() as c:
            rows = c.execute("SELECT * FROM jobs WHERE session=? AND id<? AND state!='queued' ORDER BY id DESC LIMIT 8",
                             (job['session'], job['id'])).fetchall()
            result = []
            for row in reversed(rows):
                actions = c.execute("SELECT tool,args,result,state FROM actions WHERE job_id=? AND tool!='site.describe' ORDER BY id", (row['id'],)).fetchall()
                summarized = []
                for action in actions:
                    item = dict(action)
                    if item['result'] and len(item['result']) > 6000:
                        item['result'] = encode({'note': '历史结果过大；需要时重新查询', 'truncated': True})
                    summarized.append(item)
                result.append({'message': json.loads(row['event']), 'reply': row['result'],
                               'state': row['state'], 'actions': summarized})
            return result

    def add_asset(self, asset_id, session, path, url=None):
        with self.connect() as c:
            c.execute('INSERT OR REPLACE INTO assets VALUES(?,?,?,?,?)', (asset_id, session, str(path), url, time.time()))

    def assets(self, session):
        with self.connect() as c:
            return [dict(r) for r in c.execute('SELECT * FROM assets WHERE session=? ORDER BY created DESC LIMIT 20', (session,))]

    def control(self, job, command):
        with self.connect() as c:
            if command == '/status':
                rows = c.execute('SELECT id,state FROM jobs WHERE chat_id=? AND id!=? ORDER BY id DESC LIMIT 5',
                                 (job['chat_id'], job['id'])).fetchall()
                return '\n'.join('#{} {}'.format(r['id'], r['state']) for r in rows) or '暂无任务。'
            if command == '/cancel':
                c.execute("UPDATE jobs SET cancelled=1 WHERE chat_id=? AND id!=? AND state IN ('running','queued')",
                          (job['chat_id'], job['id']))
                return '已请求停止当前会话任务；已完成的操作不会撤销。'
            if command == '/new':
                base = job['session'].rsplit(':', 1)[0]
                c.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('epoch:' + base, str(job['id'])))
                return '已开始新会话，后续消息不会沿用之前的上下文。'
            return None
