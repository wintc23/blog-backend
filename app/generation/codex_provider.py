"""Non-interactive Codex text generation with schema-constrained output."""
import json
import os
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from .configuration import GenerationError, encode


def executable():
    return shutil.which(os.environ.get('CONTENT_CODEX_BIN', 'codex'))


def response_schema(review=False):
    def obj(properties):
        return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}
    string = {'type': 'string'}
    strings = {'type': 'array', 'items': string}
    if review:
        return obj({'passed': {'type': 'boolean'}, 'issues': strings})
    section = obj({'id': string, 'group_id': {'type': 'string', 'enum': ['applications', 'development']},
                   'category': string, 'title': string, 'paragraphs': strings, 'analysis': string,
                   'source_ids': {'type': 'array', 'items': {'type': 'integer'}}})
    return obj({'title': string, 'summary': string, 'takeaways': strings,
                'sections': {'type': 'array', 'items': section}, 'closing': string,
                'cover_prompt': string, 'cover_alt': string})


def generate(model, system, inputs, request_key):
    binary = executable()
    if not binary:
        raise GenerationError('codex_missing', '找不到 Codex CLI，请安装或设置 CONTENT_CODEX_BIN')
    # Only runtime/auth/proxy configuration crosses into the generation process.
    allowed = {'HOME', 'USER', 'LOGNAME', 'PATH', 'LANG', 'LC_ALL', 'TERM', 'TMPDIR', 'CODEX_HOME',
               'XDG_CONFIG_HOME', 'CODEX_API_KEY', 'OPENAI_API_KEY', 'HTTP_PROXY', 'HTTPS_PROXY',
               'ALL_PROXY', 'NO_PROXY', 'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy'}
    environment = {key: value for key, value in os.environ.items() if key in allowed}
    with TemporaryDirectory(prefix='content-codex-') as directory:
        schema, output = Path(directory) / 'schema.json', Path(directory) / 'result.json'
        schema.write_text(encode(response_schema('article' in inputs)), encoding='utf-8')
        command = [binary, 'exec', '--ignore-user-config', '--ephemeral', '--skip-git-repo-check',
                   '--sandbox', 'read-only', '--json', '--color', 'never',
                   '-c', 'features.shell_tool=false', '-c', 'features.unified_exec=false',
                   '-c', 'web_search="disabled"', '-c', 'project_doc_max_bytes=0',
                   '--output-schema', str(schema), '--output-last-message', str(output)]
        if model['model']:
            command += ['--model', model['model']]
        command += ['-']
        prompt = system + '\n只根据下面提供的资料返回 JSON，不执行命令或读取文件。\n' + encode(inputs)
        try:
            result = subprocess.run(command, input=prompt, text=True, encoding='utf-8', capture_output=True,
                                    timeout=model['timeout'], cwd=directory, env=environment)
        except subprocess.TimeoutExpired:
            raise GenerationError('codex_timeout', 'Codex 生成超时，可重试', True)
        except OSError:
            raise GenerationError('codex_unavailable', '无法启动 Codex CLI，请检查执行路径与权限')
        if result.returncode or not output.is_file():
            raise GenerationError('codex_failed', 'Codex 执行失败（退出码 {}），请检查当前服务账户的 codex login status'.format(result.returncode), True)
        if output.stat().st_size > 2 * 1024 * 1024:
            raise GenerationError('invalid_model_json', 'Codex 输出过大')
        try:
            document = json.loads(output.read_text(encoding='utf-8'))
            if not isinstance(document, dict):
                raise ValueError()
        except (ValueError, UnicodeDecodeError):
            raise GenerationError('invalid_model_json', 'Codex 未返回有效 JSON', True)
        metadata = {'provider': 'codex', 'model': model['model'] or 'CLI default', 'client_request_id': request_key}
        for line in result.stdout.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get('type') == 'thread.started':
                metadata['response_id'] = event.get('thread_id')
            if event.get('type') == 'turn.completed':
                metadata['usage'] = event.get('usage')
        return document, metadata
