"""Small lark-cli transport. Only this class reads/sends Feishu messages."""
import hashlib
import json
import os
import subprocess
from pathlib import Path


class Lark:
    def __init__(self, binary, root, app_id):
        self.binary, self.root, self.app_id = binary, Path(root), app_id

    def environment(self):
        allowed = ('HOME', 'USER', 'PATH', 'LANG', 'LC_ALL', 'TMPDIR', 'HTTPS_PROXY', 'HTTP_PROXY', 'NO_PROXY')
        return {key: os.environ[key] for key in allowed if key in os.environ}

    def run(self, arguments, timeout=60):
        result = subprocess.run([self.binary, '--profile', self.app_id] + arguments, cwd=str(self.root), env=self.environment(),
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=timeout, encoding='utf8')
        if result.returncode:
            raise RuntimeError('飞书命令执行失败（退出码 {}）'.format(result.returncode))
        value = json.loads(result.stdout)
        if value.get('ok') is False or value.get('code', 0) != 0:
            raise RuntimeError('飞书接口返回错误')
        return value

    def verify(self):
        value = self.run(['auth', 'status'])
        if value.get('appId') != self.app_id or not value.get('identities', {}).get('bot', {}).get('available'):
            raise ValueError('飞书应用不匹配或机器人凭据不可用；服务拒绝启动')

    def message(self, message_id):
        value = self.run(['api', 'GET', '/open-apis/im/v1/messages/' + message_id, '--as', 'bot'])
        return value['data']['items'][0]

    def resource(self, message_id, key, output):
        self.run(['im', '+messages-resources-download', '--as', 'bot', '--message-id', message_id,
                  '--file-key', key, '--type', 'image', '--output', str(output)])

    def send_card(self, owner, card, dedup):
        payload = {'receive_id': owner, 'msg_type': 'interactive', 'content': json.dumps(card, ensure_ascii=False),
                   'uuid': hashlib.sha256(dedup.encode()).hexdigest()[:40]}
        return self.run(['api', 'POST', '/open-apis/im/v1/messages', '--as', 'bot',
                         '--params', '{"receive_id_type":"open_id"}', '--data', json.dumps(payload)])['data']

    def update_card(self, message_id, card):
        return self.run(['im', 'messages', 'patch', '--as', 'bot', '--message-id', message_id,
                         '--data', json.dumps({'content': json.dumps(card, ensure_ascii=False)})])

    def reply(self, row):
        dedup = hashlib.sha256(row['dedup'].encode()).hexdigest()[:40]
        arguments = ['im', '+messages-reply', '--as', 'bot', '--message-id', row['message_id'],
                     '--idempotency-key', dedup]
        if row['kind'] == 'image':
            path = Path(row['content']).resolve()
            if path.parent != (self.root / 'assets').resolve():
                raise ValueError('回传图片必须位于资产目录')
            arguments += ['--image', str(path.relative_to(self.root))]
        else:
            arguments += ['--text', row['content'][:12000]]
        self.run(arguments)

    def history(self, chat_id, start):
        page_token = None
        for _ in range(20):
            params = {'container_id_type': 'chat', 'container_id': chat_id,
                      'start_time': str(int(start)), 'sort_type': 'ByCreateTimeAsc', 'page_size': 50}
            if page_token:
                params['page_token'] = page_token
            value = self.run(['api', 'GET', '/open-apis/im/v1/messages', '--as', 'bot', '--params', json.dumps(params)])['data']
            for item in value.get('items', []):
                sender = item.get('sender', {})
                yield {'type': 'im.message.receive_v1', 'message_id': item['message_id'], 'chat_id': chat_id,
                       'chat_type': 'p2p', 'sender_id': sender.get('id'), 'sender_type': sender.get('sender_type'),
                       'create_time': item.get('create_time'), 'message_type': item.get('msg_type'),
                       'content': item.get('body', {}).get('content', '')}
            if not value.get('has_more'):
                return
            page_token = value['page_token']
        raise RuntimeError('飞书补收超过 1000 条，请检查消息积压')
