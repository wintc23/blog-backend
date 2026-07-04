import hashlib
import json
import os
import re
import shlex
import secrets
import shutil
import subprocess
import sys
import time
import uuid
import base64
import mimetypes
from datetime import datetime

from flask import Response, current_app, g, jsonify, request
from itsdangerous import TimedJSONWebSignatureSerializer as Serializer
from cryptography.fernet import Fernet
from qiniu import Auth, BucketManager, put_file

from . import api
from .decorators import ai_key_required, permission_required
from .errors import bad_request, forbidden, not_found, server_error
from .. import db
from ..qiniu import get_token
from ..models import (
  AiAccessKey,
  AiChatAttachment,
  AiChatMessage,
  AiChatSession,
  Permission,
)

AI_TOKEN_TTL = 3600 * 24
MAX_TEXT_LENGTH = 12000
MAX_IMAGE_SIZE = 8 * 1024 * 1024
ALLOWED_IMAGE_TYPES = set(['image/png', 'image/jpeg', 'image/gif', 'image/webp'])
RUNNING_CODEX_PROCESSES = {}
CODEX_OUTPUT_INSTRUCTIONS = """

输出约束：
- 不要向用户展示服务器绝对路径或工作目录。
- 如果生成了文件，只需要说明文件名和用途；系统会把文件作为可下载附件返回。
- 不要输出 markdown 本地文件链接。
"""


def _default_codex_workdir():
  backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
  workdir = os.path.abspath(os.path.join(backend_dir, '../ai'))
  if not os.path.exists(workdir):
    os.makedirs(workdir)
  return workdir


def _session_codex_workdir(session_id):
  root = os.environ.get('CODEX_WORKDIR', _default_codex_workdir())
  workdir = os.path.abspath(os.path.join(root, str(session_id)))
  if not workdir.startswith(root + os.sep):
    raise ValueError('invalid session workdir')
  if not os.path.exists(workdir):
    os.makedirs(workdir)
  return workdir


def _delete_session_codex_workdir(session_id):
  root = os.environ.get('CODEX_WORKDIR', _default_codex_workdir())
  workdir = os.path.abspath(os.path.join(root, str(session_id)))
  if not workdir.startswith(root + os.sep):
    return
  try:
    if os.path.exists(workdir):
      shutil.rmtree(workdir)
  except Exception:
    current_app.logger.exception('Delete session codex workdir failed')


def _hash_key(raw_key):
  secret = current_app.config['SECRET_KEY']
  return hashlib.sha256((raw_key + secret).encode('utf-8')).hexdigest()


def _preview_key(raw_key):
  return raw_key[:12] + '...' + raw_key[-6:]


def _fernet():
  digest = hashlib.sha256(current_app.config['SECRET_KEY'].encode('utf-8')).digest()
  return Fernet(base64.urlsafe_b64encode(digest))


def _encrypt_key(raw_key):
  return _fernet().encrypt(raw_key.encode('utf-8')).decode('utf-8')


def _decrypt_key(encrypted):
  if not encrypted:
    return ''
  try:
    return _fernet().decrypt(encrypted.encode('utf-8')).decode('utf-8')
  except:
    return ''


def _generate_key():
  return 'codex_sk_' + secrets.token_urlsafe(32)


def _generate_ai_token(ai_key):
  s = Serializer(current_app.config['SECRET_KEY'], expires_in=AI_TOKEN_TTL)
  return s.dumps({'type': 'ai_key', 'id': ai_key.id}).decode('utf-8')


def _parse_expires_at(value):
  if not value:
    return None
  if isinstance(value, (int, float)):
    return datetime.utcfromtimestamp(value)
  try:
    return datetime.strptime(value[:10], '%Y-%m-%d')
  except:
    return None


def _current_session(session_id):
  session = AiChatSession.query.get(session_id)
  if not session or session.status == 'deleted':
    return None
  if session.access_key_id != g.current_ai_key.id:
    return None
  return session


def _session_title(content):
  title = (content or '').strip().replace('\n', ' ')
  if not title:
    return '新的会话'
  return title[:40]


def _attachment_url(file_key):
  return '/api/ai/attachments/' + file_key


def _qiniu_ai_key(file_key):
  prefix = os.environ.get('AI_QINIU_PREFIX', 'ai-chat/').strip()
  if prefix and not prefix.endswith('/'):
    prefix += '/'
  return prefix + file_key


def _qiniu_file_url(file_key):
  return current_app.config['QI_NIU_LINK_URL'].rstrip('/') + '/' + _qiniu_ai_key(file_key)


def _upload_local_file_to_qiniu(local_path, file_key):
  token = get_token(_qiniu_ai_key(file_key))
  ret, info = put_file(token, _qiniu_ai_key(file_key), local_path)
  if not ret or not ret.get('key'):
    current_app.logger.error('Upload generated file to qiniu failed: %s', info)
    return ''
  return current_app.config['QI_NIU_LINK_URL'].rstrip('/') + '/' + ret['key']


def _delete_ai_file(file_key):
  dirname, _ = os.path.split(os.path.abspath(sys.argv[0]))
  local_path = os.path.abspath(os.path.join(dirname, '../files/ai', file_key))
  root = os.path.abspath(os.path.join(dirname, '../files/ai'))
  if not local_path.startswith(root + os.sep):
    return
  try:
    if os.path.exists(local_path):
      os.remove(local_path)
  except Exception:
    current_app.logger.exception('Delete local AI image failed')

  try:
    access_key = current_app.config['QI_NIU_ACCESS_KEY']
    secret_key = current_app.config['QI_NIU_SECRET_KEY']
    bucket = current_app.config['QI_NIU_BUCKET']
    q = Auth(access_key, secret_key)
    BucketManager(q).delete(bucket, _qiniu_ai_key(file_key))
  except Exception:
    current_app.logger.exception('Delete qiniu AI image failed')


def _delete_session_data(session):
  messages = session.messages.all()
  message_ids = [item.id for item in messages]
  if message_ids:
    attachments = AiChatAttachment.query.filter(AiChatAttachment.message_id.in_(message_ids)).all()
    for attachment in attachments:
      _delete_ai_file(attachment.file_key)
      db.session.delete(attachment)
    for message in messages:
      db.session.delete(message)
  _delete_session_codex_workdir(session.id)


class CodexClient(object):
  def __init__(self):
    self.command = os.environ.get('CODEX_COMMAND', '/usr/local/bin/codex' if os.path.exists('/usr/local/bin/codex') else 'codex')
    self.timeout = int(os.environ.get('CODEX_TIMEOUT', '120'))

  def send_message(self, chat_session, content, attachments):
    prompt = (content or '') + CODEX_OUTPUT_INSTRUCTIONS
    if attachments:
      prompt += '\n\n附件：\n' + '\n'.join([item.file_url or item.file_key for item in attachments])

    cwd = _session_codex_workdir(chat_session.id)
    before_files = self._snapshot_files(cwd)
    command = self._build_command(chat_session.codex_session_id, prompt, attachments, cwd)
    started = time.time()
    proc = None
    try:
      proc = subprocess.Popen(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=self._env(),
        universal_newlines=True,
      )
      RUNNING_CODEX_PROCESSES[chat_session.id] = proc
      stdout, stderr = proc.communicate(timeout=self.timeout)
    except subprocess.TimeoutExpired:
      if proc:
        proc.kill()
      return {
        'ok': False,
        'content': 'Codex 调用超时，请稍后重试。',
        'metadata': {'error': 'timeout', 'timeout': self.timeout}
      }
    except Exception as e:
      current_app.logger.exception('Codex process failed before completion')
      return {
        'ok': False,
        'content': 'Codex 调用失败，请稍后重试。',
        'metadata': {'error': 'process_failed'}
      }
    finally:
      RUNNING_CODEX_PROCESSES.pop(chat_session.id, None)

    parsed = self._parse_stdout(stdout)
    if parsed.get('session_id'):
      chat_session.codex_session_id = parsed['session_id']
    generated_files = self._collect_generated_files(chat_session.id, cwd, before_files)

    metadata = {
      'exit_code': proc.returncode,
      'duration': round(time.time() - started, 3),
      'codex_session_id': chat_session.codex_session_id,
      'generated_files': generated_files,
    }
    if proc.returncode != 0:
      current_app.logger.error('Codex process exited with %s: %s', proc.returncode, stderr[-4000:])
      return {
        'ok': False,
        'content': 'Codex 调用失败，请稍后重试。',
        'metadata': metadata
      }
    return {
      'ok': True,
      'content': self._sanitize_content(parsed.get('content') or stdout.strip() or 'Codex 没有返回内容。', cwd),
      'metadata': metadata
    }

  def _build_command(self, session_id, prompt, attachments, cwd):
    template = os.environ.get('CODEX_COMMAND_TEMPLATE')
    if template:
      return shlex.split(template.format(session_id=session_id, prompt=prompt))
    if session_id:
      command = [self.command, 'exec', 'resume', '--json', '--skip-git-repo-check']
    else:
      command = [self.command, 'exec', '--json', '--skip-git-repo-check', '-C', cwd]
    for item in attachments:
      path = self._attachment_path(item)
      if path and os.path.exists(path):
        command += ['-i', path]
    if session_id:
      command.append(session_id)
    command.append(prompt)
    return command

  def _attachment_path(self, attachment):
    dirname, _ = os.path.split(os.path.abspath(sys.argv[0]))
    return os.path.abspath(os.path.join(dirname, '../files/ai', attachment.file_key))

  def _snapshot_files(self, cwd):
    result = {}
    for root, dirs, files in os.walk(cwd):
      dirs[:] = [d for d in dirs if d not in ['.git', 'node_modules', '.next']]
      for filename in files:
        path = os.path.join(root, filename)
        try:
          result[path] = os.path.getmtime(path)
        except:
          pass
    return result

  def _collect_generated_files(self, session_id, cwd, before_files):
    generated = []
    for root, dirs, files in os.walk(cwd):
      dirs[:] = [d for d in dirs if d not in ['.git', 'node_modules', '.next']]
      for filename in files:
        path = os.path.join(root, filename)
        try:
          mtime = os.path.getmtime(path)
          if path in before_files and mtime <= before_files[path]:
            continue
          if os.path.getsize(path) > 50 * 1024 * 1024:
            continue
          rel = os.path.relpath(path, cwd)
          safe_name = rel.replace('\\', '/').replace('../', '').strip('/')
          file_key = str(session_id) + '/generated/' + str(uuid.uuid4()).replace('-', '') + '_' + safe_name.replace('/', '_')
          file_url = _upload_local_file_to_qiniu(path, file_key)
          if not file_url:
            continue
          mime_type = mimetypes.guess_type(path)[0] or 'application/octet-stream'
          generated.append({
            'file_key': file_key,
            'file_url': file_url,
            'mime_type': mime_type,
            'size': os.path.getsize(path),
            'name': os.path.basename(path),
          })
        except Exception:
          current_app.logger.exception('Collect generated file failed')
    return generated

  def _sanitize_content(self, content, cwd):
    text = content or ''
    text = re.sub(r'\[[^\]]+\]\((?:file://)?' + re.escape(cwd) + r'[^)]*\)', '已生成文件，见下方附件。', text)
    text = text.replace(cwd + os.sep, '')
    text = text.replace(cwd, '会话目录')
    root = os.environ.get('CODEX_WORKDIR', _default_codex_workdir())
    text = re.sub(r'\[[^\]]+\]\((?:file://)?' + re.escape(root) + r'[^)]*\)', '已生成文件，见下方附件。', text)
    text = text.replace(root + os.sep, '')
    text = text.replace(root, 'AI 工作目录')
    return text

  def _parse_stdout(self, stdout):
    content = ''
    session_id = ''
    for line in stdout.splitlines():
      try:
        event = json.loads(line)
      except:
        continue
      session_id = event.get('session_id') or event.get('sessionId') or event.get('thread_id') or session_id
      event_type = event.get('type') or ''
      if event_type in ['agent_message', 'message']:
        value = event.get('message') or event.get('content') or ''
        if isinstance(value, list):
          value = ''.join([part.get('text', '') if isinstance(part, dict) else str(part) for part in value])
        if value:
          content = str(value)
      if event_type in ['session_configured', 'session.started']:
        session_id = event.get('session_id') or event.get('sessionId') or event.get('thread_id') or session_id
      item = event.get('item')
      if isinstance(item, dict):
        session_id = item.get('session_id') or item.get('sessionId') or item.get('thread_id') or session_id
        if item.get('type') in ['message', 'agent_message'] and item.get('role') == 'assistant':
          parts = item.get('content') or []
          texts = []
          for part in parts:
            if isinstance(part, dict):
              texts.append(part.get('text') or part.get('content') or '')
            else:
              texts.append(str(part))
          if texts:
            content = ''.join(texts)
        if item.get('type') == 'agent_message' and item.get('text'):
          content = item.get('text')
    return {'content': content.strip(), 'session_id': session_id}

  def _env(self):
    allowed = [
      'PATH',
      'HOME',
      'LANG',
      'LC_ALL',
      'OPENAI_API_KEY',
      'CODEX_HOME',
      'CODEX_AUTH_TOKEN',
    ]
    env = dict([(key, os.environ[key]) for key in allowed if key in os.environ])
    env['PATH'] = '/usr/local/bin:' + env.get('PATH', '')
    return env


@api.route('/ai/auth', methods=['POST'])
def ai_auth():
  raw_key = (request.json or {}).get('key', '').strip()
  if not raw_key:
    return bad_request('请输入访问 key')
  ai_key = AiAccessKey.query.filter_by(key_hash=_hash_key(raw_key)).first()
  if not ai_key or not ai_key.is_available():
    return forbidden('访问 key 无效或已停用')
  ai_key.last_used_at = datetime.utcnow()
  db.session.add(ai_key)
  return jsonify({'token': _generate_ai_token(ai_key), 'expires_in': AI_TOKEN_TTL})


@api.route('/ai/sessions')
@ai_key_required
def ai_sessions():
  sessions = AiChatSession.query.filter_by(
    access_key_id=g.current_ai_key.id,
    status='active'
  ).order_by(AiChatSession.pinned.desc(), AiChatSession.updated_at.desc()).all()
  return jsonify({'list': [item.to_json() for item in sessions]})


@api.route('/ai/sessions', methods=['POST'])
@ai_key_required
def create_ai_session():
  title = (request.json or {}).get('title') or '新的会话'
  session = AiChatSession(access_key_id=g.current_ai_key.id, title=title[:128])
  db.session.add(session)
  db.session.flush()
  return jsonify(session.to_json())


@api.route('/ai/sessions/<int:session_id>', methods=['PATCH'])
@ai_key_required
def update_ai_session(session_id):
  session = _current_session(session_id)
  if not session:
    return not_found('会话不存在')
  data = request.json or {}
  if 'title' in data:
    title = (data.get('title') or '').strip()
    if not title:
      return bad_request('请输入会话名称')
    session.title = title[:128]
  if 'pinned' in data:
    session.pinned = bool(data.get('pinned'))
  session.updated_at = datetime.utcnow()
  db.session.add(session)
  return jsonify(session.to_json())


@api.route('/ai/sessions/<int:session_id>', methods=['DELETE'])
@ai_key_required
def delete_ai_session(session_id):
  session = _current_session(session_id)
  if not session:
    return not_found('会话不存在')
  _delete_session_data(session)
  session.status = 'deleted'
  session.updated_at = datetime.utcnow()
  db.session.add(session)
  return jsonify({'success': True})


@api.route('/ai/sessions/<int:session_id>/messages')
@ai_key_required
def ai_messages(session_id):
  session = _current_session(session_id)
  if not session:
    return not_found('会话不存在')
  messages = session.messages.order_by(AiChatMessage.created_at.asc(), AiChatMessage.id.asc()).all()
  return jsonify({'list': [item.to_json() for item in messages]})


@api.route('/ai/sessions/<int:session_id>/messages', methods=['POST'])
@ai_key_required
def send_ai_message(session_id):
  session = _current_session(session_id)
  if not session:
    return not_found('会话不存在')
  data = request.json or {}
  content = (data.get('content') or '').strip()
  attachments = data.get('attachments') or []
  if not content and not attachments:
    return bad_request('请输入消息内容')
  if len(content) > MAX_TEXT_LENGTH:
    return bad_request('消息过长')
  if session.id in RUNNING_CODEX_PROCESSES:
    return bad_request('当前会话正在回复，请稍后再发送')

  user_message = AiChatMessage(
    session_id=session.id,
    role='user',
    content=content,
    content_type='mixed' if attachments else 'text',
  )
  db.session.add(user_message)
  db.session.flush()

  saved_attachments = []
  for item in attachments:
    file_key = (item.get('file_key') or '').strip()
    if not file_key:
      continue
    attachment = AiChatAttachment(
      message_id=user_message.id,
      file_key=file_key,
      file_url=item.get('file_url') or _attachment_url(file_key),
      mime_type=item.get('mime_type'),
      size=item.get('size'),
    )
    db.session.add(attachment)
    saved_attachments.append(attachment)
  if saved_attachments:
    db.session.flush()

  if session.title == '新的会话':
    session.title = _session_title(content)
  session.touch()
  result = CodexClient().send_message(session, content, saved_attachments)
  assistant_message = AiChatMessage(
    session_id=session.id,
    role='assistant' if result['ok'] else 'error',
    content=result['content'],
    content_type='text',
    status='completed' if result['ok'] else 'failed',
    metadata_json=json.dumps(result.get('metadata') or {}),
  )
  db.session.add(assistant_message)
  db.session.add(session)
  db.session.flush()
  for item in (result.get('metadata') or {}).get('generated_files') or []:
    attachment = AiChatAttachment(
      message_id=assistant_message.id,
      file_key=item.get('file_key'),
      file_url=item.get('file_url'),
      mime_type=item.get('mime_type'),
      size=item.get('size'),
    )
    db.session.add(attachment)
  db.session.flush()
  return jsonify({
    'session': session.to_json(),
    'user_message': user_message.to_json(),
    'assistant_message': assistant_message.to_json()
  })


@api.route('/ai/sessions/<int:session_id>/stop', methods=['POST'])
@ai_key_required
def stop_ai_message(session_id):
  session = _current_session(session_id)
  if not session:
    return not_found('会话不存在')
  proc = RUNNING_CODEX_PROCESSES.get(session.id)
  if proc and proc.poll() is None:
    proc.kill()
    RUNNING_CODEX_PROCESSES.pop(session.id, None)
    return jsonify({'success': True, 'stopped': True})
  return jsonify({'success': True, 'stopped': False})


@api.route('/ai/qiniu-token', methods=['POST'])
@ai_key_required
def create_ai_qiniu_token():
  data = request.json or {}
  session_id = data.get('session_id')
  session = _current_session(session_id) if session_id else None
  if not session:
    return not_found('会话不存在')
  ext = os.path.splitext(data.get('filename') or '')[1].lower()
  if ext not in ['.png', '.jpg', '.jpeg', '.gif', '.webp']:
    ext = '.png'
  filename = str(uuid.uuid4()).replace('-', '') + ext
  file_key = str(session.id) + '/' + filename
  qiniu_key = _qiniu_ai_key(file_key)
  token = get_token(qiniu_key)
  return jsonify({
    'file_key': file_key,
    'qiniu_key': qiniu_key,
    'token': token,
    'upload_url': os.environ.get('AI_QINIU_UPLOAD_URL', 'https://up-z2.qiniup.com/'),
    'file_url': current_app.config['QI_NIU_LINK_URL'].rstrip('/') + '/' + qiniu_key,
  })


@api.route('/ai/upload', methods=['POST'])
@ai_key_required
def upload_ai_attachment():
  session_id = request.form.get('session_id', type=int)
  session = _current_session(session_id) if session_id else None
  if not session:
    return not_found('会话不存在')
  if 'file' not in request.files:
    return bad_request('请选择文件')
  f = request.files['file']
  mime_type = f.mimetype or ''
  if mime_type not in ALLOWED_IMAGE_TYPES:
    return bad_request('只支持 png、jpg、gif、webp 图片')
  f.seek(0, os.SEEK_END)
  size = f.tell()
  f.seek(0)
  if size > MAX_IMAGE_SIZE:
    return bad_request('图片不能超过 8MB')
  file_key = (request.form.get('file_key') or '').strip()
  if not file_key or not file_key.startswith(str(session.id) + '/'):
    return bad_request('图片路径无效')
  filename = os.path.basename(file_key)
  ext = os.path.splitext(filename)[1].lower()
  if ext not in ['.png', '.jpg', '.jpeg', '.gif', '.webp']:
    return bad_request('图片类型无效')
  dirname, _ = os.path.split(os.path.abspath(sys.argv[0]))
  upload_path = os.path.abspath(os.path.join(dirname, '../files/ai', str(session.id)))
  if not os.path.exists(upload_path):
    os.makedirs(upload_path)
  local_path = os.path.join(upload_path, filename)
  f.save(local_path)
  file_url = request.form.get('file_url') or _attachment_url(file_key)
  return jsonify({
    'file_key': file_key,
    'file_url': file_url,
    'mime_type': mime_type,
    'size': size
  })


@api.route('/ai/attachments/<path:filename>')
def get_ai_attachment(filename):
  from flask import send_from_directory
  dirname, _ = os.path.split(os.path.abspath(sys.argv[0]))
  upload_path = os.path.abspath(os.path.join(dirname, '../files/ai'))
  return send_from_directory(upload_path, filename)


@api.route('/manage/ai-keys')
@permission_required(Permission.ADMIN)
def manage_ai_keys():
  keys = AiAccessKey.query.order_by(AiAccessKey.created_at.desc()).all()
  result = []
  for item in keys:
    data = item.to_json()
    data['key'] = _decrypt_key(item.key_encrypted)
    result.append(data)
  return jsonify({'list': result})


@api.route('/manage/ai-keys', methods=['POST'])
@permission_required(Permission.ADMIN)
def create_ai_key():
  data = request.json or {}
  name = (data.get('name') or '').strip()
  if not name:
    return bad_request('请输入 key 名称')
  raw_key = _generate_key()
  item = AiAccessKey(
    name=name,
    key_hash=_hash_key(raw_key),
    key_preview=_preview_key(raw_key),
    key_encrypted=_encrypt_key(raw_key),
    enabled=bool(data.get('enabled', True)),
    usage_limit=data.get('usage_limit'),
    expires_at=_parse_expires_at(data.get('expires_at')),
    created_by_id=g.current_user.id if g.current_user else None,
  )
  db.session.add(item)
  db.session.flush()
  result = item.to_json()
  result['key'] = raw_key
  return jsonify(result)


@api.route('/manage/ai-keys/<int:key_id>', methods=['PATCH'])
@permission_required(Permission.ADMIN)
def update_ai_key(key_id):
  item = AiAccessKey.query.get(key_id)
  if not item:
    return not_found('key 不存在')
  data = request.json or {}
  if 'name' in data:
    item.name = (data.get('name') or '').strip()[:128]
  if 'enabled' in data:
    item.enabled = bool(data.get('enabled'))
  if 'usage_limit' in data:
    item.usage_limit = data.get('usage_limit')
  if 'expires_at' in data:
    item.expires_at = _parse_expires_at(data.get('expires_at'))
  db.session.add(item)
  return jsonify(item.to_json())


@api.route('/manage/ai-keys/<int:key_id>', methods=['DELETE'])
@permission_required(Permission.ADMIN)
def delete_ai_key(key_id):
  item = AiAccessKey.query.get(key_id)
  if not item:
    return not_found('key 不存在')
  for session in item.sessions.all():
    _delete_session_data(session)
    db.session.delete(session)
  db.session.delete(item)
  return jsonify({'success': True})
