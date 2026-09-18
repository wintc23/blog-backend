"""One durable owner-only listener, one serial worker, one reply sender."""
import json
import logging
import os
import re
import selectors
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .media import Media
from .planner import Cancelled, Planner
from .site import Site, redact
from .store import Store
from .lark import Lark
from .configuration import public_links
from .reviews import Reviews

LOG = logging.getLogger('lark-bridge')


def accept(event, owner, started):
    return (isinstance(event, dict) and event.get('type') == 'im.message.receive_v1'
            and event.get('sender_type') == 'user' and event.get('sender_id') == owner
            and event.get('chat_type') == 'p2p'
            and bool(re.fullmatch(r'om_[a-zA-Z0-9_-]+', event.get('message_id', '')))
            and bool(re.fullmatch(r'oc_[a-zA-Z0-9_-]+', event.get('chat_id', '')))
            and str(event.get('create_time', '')).isdigit()
            and float(event['create_time']) / 1000 >= started)


def image_keys(value):
    result = []
    if isinstance(value, dict):
        if isinstance(value.get('image_key'), str) and value['image_key'].startswith('img_'):
            result.append(value['image_key'])
        for key, item in value.items():
            if key != 'image_key':
                result.extend(image_keys(item))
    elif isinstance(value, list):
        for item in value:
            result.extend(image_keys(item))
    return list(dict.fromkeys(result))


class Bridge:
    def __init__(self, app, store=None, lark=None, planner=None):
        self.owner = os.environ['LARK_BRIDGE_OWNER_OPEN_ID']
        self.app_id = os.environ['LARK_BRIDGE_APP_ID']
        public_links()  # Fail early on invalid deployment configuration.
        self.store = store or Store(os.environ.get('LARK_BRIDGE_STATE_DIR', 'var/lark-bridge'))
        self.site = Site(app, int(os.environ['LARK_BRIDGE_ADMIN_USER_ID']))
        self.media = Media(self.store, app)
        self.lark = lark or Lark(os.environ.get('LARK_BRIDGE_CLI', 'lark-cli'), self.store.root, self.app_id)
        self.planner = planner or Planner(os.environ.get('CONTENT_CODEX_BIN', 'codex'))
        self.reviews = Reviews(self.store, self.site, self.lark, self.owner, self.app_id)
        self.stop = threading.Event()
        self.ready = False
        self.card_ready = False
        with self.store.connect() as c:
            self.started = float(c.execute("SELECT value FROM meta WHERE key='started'").fetchone()[0])

    def ingest(self, event):
        if not accept(event, self.owner, self.started):
            return None
        if len(json.dumps(event)) > 64000:
            return None
        content = event.get('content', '')
        try:
            content = json.loads(content).get('text', content)
        except (ValueError, TypeError, AttributeError):
            pass
        control = isinstance(content, str) and content.strip() in ('/status', '/cancel', '/new')
        job_id = self.store.enqueue(event, 'control' if control else 'queued')
        if not job_id:
            return None
        LOG.info('accepted job=%s message=%s', job_id, event['message_id'])
        if control:
            with self.store.connect() as c:
                job = dict(c.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone())
            result = self.store.control(job, content.strip())
            self.store.finish(job, 'done', result)
        else:
            self.store.reply(event['message_id'], '已收到，任务 #{} 正在排队处理。'.format(job_id), 'ack:' + str(job_id))
        return job_id

    def prepare(self, job):
        message = self.lark.message(job['message_id'])
        sender = message.get('sender', {})
        if sender.get('id') != self.owner or sender.get('sender_type') != 'user' or message.get('chat_id') != job['chat_id']:
            raise ValueError('原始消息身份核验失败')
        content = json.loads(message.get('body', {}).get('content') or '{}')
        for index, key in enumerate(image_keys(content)[:4]):
            output = self.store.root / ('download-{}-{}.image'.format(job['id'], index))
            try:
                self.lark.resource(job['message_id'], key, output)
                if output.stat().st_size > 20 * 1024 * 1024:
                    raise ValueError('附件图片超过 20 MB')
                self.media.save(job['session'], output.read_bytes())
            finally:
                if output.exists():
                    output.unlink()
        return content

    def execute(self, job, tool, arguments, step):
        # Recheck admin on every tool, including file upload/image generation.
        self.site.admin_token()
        if tool == 'site.describe':
            return self.site.describe(arguments['name'])
        if tool == 'site.request':
            review = re.fullmatch(r'/api/set-(comment|message)-show/(\d+)', arguments.get('path', ''))
            if review:
                return self.reviews.request(review.group(1), int(review.group(2)))
            return self.site.request(arguments)
        if tool == 'assets.upload':
            return self.media.upload(job['session'], arguments['asset_id'])
        if tool in ('images.edit', 'images.generate'):
            result = self.media.generate(job['session'], arguments['prompt'], 'lark-{}-{}'.format(job['id'], step),
                                         arguments['asset_id'] if tool == 'images.edit' else None)
            _, path = self.media.get(job['session'], result['asset_id'])
            self.store.reply(job['message_id'], str(path), 'image:{}:{}'.format(job['id'], step), 'image')
            return result
        raise ValueError('不支持的工具')

    def work(self, job):
        observations = []
        def cancelled():
            return self.stop.is_set() or self.store.cancelled(job['id'])
        try:
            if cancelled():
                raise Cancelled('任务已取消。')
            content = self.prepare(job)
            history = self.store.history(job)
            started = time.monotonic()
            for step in range(12):
                if cancelled():
                    raise Cancelled('任务已停止，已完成的操作保留。')
                if time.monotonic() - started > 1800:
                    raise RuntimeError('任务超过 30 分钟，已停止后续操作。')
                assets = self.store.assets(job['session'])
                context = {'today': datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d'), 'public_links': public_links(),
                           'message': content, 'history': history, 'catalog': self.site.catalog(),
                           'assets': [{'asset_id': a['id'], 'url': a['url']} for a in assets], 'results': observations}
                action = self.planner.decide(context, [a['path'] for a in assets[:4]], cancelled)
                if cancelled():
                    raise Cancelled('任务已停止，已完成的操作保留。')
                if action['tool'] == 'final':
                    self.store.finish(job, 'done', action['reply'][:12000] or '任务已结束。')
                    return
                previous = next((item for item in observations if item['tool'] == action['tool']
                                 and item['arguments'] == action['arguments']), None)
                if previous and action['tool'] != 'site.describe' and not (
                        action['tool'] == 'site.request' and action['arguments'].get('method', 'GET').upper() == 'GET'):
                    observations.append(dict(previous, reused=True))
                    continue
                action_id = self.store.action(job['id'], action['tool'], redact(action['arguments']))
                # Runtime/transport exceptions stop the task: a timed-out write
                # might already have committed, so the planner must not retry it.
                try:
                    result = self.execute(job, action['tool'], action['arguments'], step)
                except (ValueError, KeyError) as error:
                    result = {'error': str(error)[:500], 'executed': False}
                self.store.action_result(action_id, result)
                observations.append({'tool': action['tool'], 'arguments': redact(action['arguments']), 'result': result})
            raise RuntimeError('已达到单次任务步骤上限，请根据已完成结果继续。')
        except Cancelled as error:
            self.store.finish(job, 'cancelled', str(error))
        except Exception as error:
            LOG.error('job=%s failed type=%s', job['id'], type(error).__name__)
            # Avoid disclosing SQL/provider errors, credentials or local paths.
            detail = str(error) if type(error) in (ValueError, RuntimeError) else '处理失败，请查看服务日志并检查配置。'
            self.store.finish(job, 'failed', '任务 #{}：{} 已完成的操作保留，不会自动重做。'.format(job['id'], detail[:500]))

    def worker(self):
        while not self.stop.is_set():
            job = self.store.claim()
            if job:
                self.work(job)
            else:
                self.stop.wait(0.5)

    def sender(self):
        while not self.stop.is_set():
            row = self.store.pending_reply()
            if not row:
                self.stop.wait(1)
                continue
            try:
                if row['kind'] in ('review_card', 'review_update'):
                    self.reviews.send(row)
                else:
                    self.lark.reply(row)
                self.store.reply_result(row, True)
            except Exception as error:
                LOG.warning('reply=%s failed type=%s', row['id'], type(error).__name__)
                self.store.reply_result(row, False)

    def backfill(self):
        with self.store.connect() as c:
            chats = c.execute('SELECT DISTINCT chat_id FROM jobs').fetchall()
        for row in chats:
            if self.stop.is_set():
                return
            chat = row['chat_id']
            key = 'cursor:' + chat
            with self.store.connect() as c:
                cursor = c.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
            start = max(self.started, float(cursor['value']) - 120 if cursor else self.started)
            end = time.time()
            try:
                for event in self.lark.history(chat, start):
                    self.ingest(event)
                with self.store.connect() as c:
                    c.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (key, str(end)))
            except Exception as error:
                LOG.warning('backfill failed type=%s', type(error).__name__)

    def recover_loop(self):
        while not self.stop.is_set():
            if self.ready:
                self.backfill()
            self.stop.wait(60)

    def review_loop(self):
        next_scan = 0
        while not self.stop.is_set():
            if self.card_ready and time.monotonic() >= next_scan:
                try:
                    self.reviews.scan()
                except Exception as error:
                    LOG.warning('review scan failed type=%s', type(error).__name__)
                next_scan = time.monotonic() + 30
            while not self.stop.is_set() and self.reviews.work_one():
                pass
            self.stop.wait(1)

    def listen_cards(self):
        self.listen('card.action.trigger', self.reviews.handle)

    def listen(self, event_key='im.message.receive_v1', handler=None):
        handler = handler or self.ingest
        delay = 2
        while not self.stop.is_set():
            self.lark.verify()
            process = subprocess.Popen([self.lark.binary, '--profile', self.app_id, 'event', 'consume', event_key, '--as', 'bot'],
                                            cwd=str(self.store.root), env=self.lark.environment(), stdin=subprocess.PIPE,
                                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
            selector = selectors.DefaultSelector()
            selector.register(process.stdout, selectors.EVENT_READ, 'stdout')
            selector.register(process.stderr, selectors.EVENT_READ, 'stderr')
            buffers = {'stdout': b'', 'stderr': b''}
            ready = False
            connected = time.monotonic()
            try:
                while not self.stop.is_set() and process.poll() is None:
                    if not ready and time.monotonic() - connected > 90:
                        LOG.error('listener readiness timeout')
                        break
                    for key, _ in selector.select(timeout=1):
                        chunk = os.read(key.fileobj.fileno(), 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        stream = key.data
                        buffers[stream] += chunk
                        if len(buffers[stream]) > 1024 * 1024:
                            raise RuntimeError('飞书事件流超过单行大小限制')
                        while b'\n' in buffers[stream]:
                            line, buffers[stream] = buffers[stream].split(b'\n', 1)
                            if stream == 'stderr':
                                if b'[event] ready event_key=' in line:
                                    ready = True
                                    if event_key == 'card.action.trigger':
                                        self.card_ready = True
                                    else:
                                        self.ready = True
                                    delay = 2
                                    LOG.info('listener ready app=%s event=%s', self.app_id, event_key)
                                elif b'drop' in line.lower() or b'error' in line.lower() or b'fail' in line.lower():
                                    LOG.warning('listener diagnostic: %s', line.decode('utf8', 'replace')[:400])
                            else:
                                try:
                                    handler(json.loads(line))
                                except (ValueError, TypeError):
                                    LOG.warning('invalid event ignored')
            finally:
                if event_key == 'card.action.trigger':
                    self.card_ready = False
                else:
                    self.ready = False
                selector.close()
                process.stdin.close()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=10)
                process.stdout.close()
                process.stderr.close()
            if not self.stop.is_set():
                LOG.warning('listener disconnected, reconnecting')
                self.stop.wait(delay)
                delay = min(60, delay * 2)

    def run(self):
        self.site.admin_token()
        self.lark.verify()
        self.store.recover()
        self.reviews.recover()
        failures = []
        def supervise(target):
            try:
                target()
                if not self.stop.is_set():
                    raise RuntimeError('background worker stopped unexpectedly')
            except Exception as error:
                failures.append(type(error).__name__)
                LOG.error('background worker failed type=%s', type(error).__name__)
                self.stop.set()
        threads = [threading.Thread(target=supervise, args=(target,), daemon=True)
                   for target in (self.worker, self.sender, self.recover_loop, self.review_loop, self.listen_cards)]
        for thread in threads:
            thread.start()
        try:
            self.listen()
        finally:
            self.stop.set()
            for thread in threads:
                thread.join(timeout=8)
        if failures:
            raise RuntimeError('桥接后台进程异常，退出以便服务管理器重启')
