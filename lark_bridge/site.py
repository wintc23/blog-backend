"""Dispatch to real Flask routes using a short-lived administrator credential."""
import ast
import inspect
import json
import re
from urllib.parse import unquote, urlsplit


SECRET = re.compile(r'(secret|password|authorization|access.?token|refresh.?token|api.?key|^token$|^key$|encrypted)', re.I)
# Authentication/session endpoints are not website business operations. File
# downloads/uploads use asset tools, avoiding arbitrary local filesystem reads.
EXCLUDED = ('/api/ai/', '/api/get-file/', '/api/get-qiniu-token/', '/api/save-image/',
            '/api/auth', '/api/login', '/api/logout', '/api/qq', '/api/github', '/api/email-login', '/api/guest-login')


def redact(value):
    if isinstance(value, dict):
        return {k: '[REDACTED]' if SECRET.search(k) else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


class Site:
    def __init__(self, app, admin_id):
        self.app, self.admin_id = app, admin_id
        self.rules = {r.endpoint: r for r in app.url_map.iter_rules()
                      if r.rule.startswith('/api/') and not r.rule.startswith(EXCLUDED)}
        if not app.extensions.get('lark_review_guard'):
            from flask import request
            def review_guard():
                guard = request.environ.get('lark.review.guard')
                if guard:
                    return guard()
            app.before_request_funcs.setdefault('api', []).append(review_guard)
            app.extensions['lark_review_guard'] = True

    def admin_token(self):
        from app.models import User, Permission
        from app import db
        with self.app.app_context():
            try:
                user = User.query.get(self.admin_id)
                if not user or not user.can(Permission.ADMIN):
                    raise ValueError('绑定的网站账号已失去管理员权限')
                return user.generate_auth_token(60)
            finally:
                db.session.remove()

    def catalog(self):
        return [{'path': r.rule, 'methods': sorted(r.methods - {'HEAD', 'OPTIONS'}), 'name': r.endpoint}
                for r in self.app.url_map.iter_rules() if r.endpoint in self.rules]

    def resolve(self, path, method):
        if (not isinstance(path, str) or len(path) > 2048 or not path.startswith('/api/')
                or any(x in unquote(path) for x in ('..', '\\', '\x00', '\r', '\n'))):
            raise ValueError('只能调用目录中列出的网站 API 路径')
        parsed = urlsplit(path)
        if parsed.scheme or parsed.netloc or parsed.fragment or parsed.query:
            raise ValueError('查询参数须放入 query，不能传入网址')
        endpoint, _ = self.app.url_map.bind('localhost').match(path, method=method)
        if endpoint not in self.rules:
            raise ValueError('此接口使用专用认证或文件工具，不开放通用调用')
        return endpoint

    def describe(self, name):
        if name not in self.rules:
            raise ValueError('未知接口，请使用目录中的 name')
        function = inspect.unwrap(self.app.view_functions[name])
        module = inspect.getmodule(function)
        # Only function definitions from the API module, never environment or
        # credential files. Include local validators used by route handlers.
        source = inspect.getsource(function)
        tree = ast.parse(inspect.getsource(module))
        called = {n.func.id for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in called:
                helper = getattr(module, node.name, None)
                if helper:
                    source += '\n' + inspect.getsource(helper)
        return {'name': name, 'implementation': source[:22000]}

    def request(self, arguments, guard=None):
        method = arguments.get('method', 'GET').upper()
        if method not in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE'):
            raise ValueError('不支持的 HTTP 方法')
        path = arguments['path']
        endpoint = self.resolve(path, method)
        if endpoint in ('api.set_comment_show', 'api.set_message_show') and guard is None:
            raise ValueError('审核通过必须由 Owner 点击审核卡片')
        body, query = arguments.get('body'), arguments.get('query', {})
        if not isinstance(query, dict) or len(json.dumps(body)) > 128000:
            raise ValueError('接口参数格式错误或过大')
        token = self.admin_token()
        try:
            with self.app.test_client() as client:
                response = client.open(path, method=method, query_string=query, json=body,
                                       headers={'Authorization': 'Bearer ' + token}, follow_redirects=False,
                                       environ_overrides={'lark.review.guard': guard} if guard else {})
                if response.status_code >= 500:
                    raise RuntimeError('网站接口异常；操作结果需要核实，不自动重试。')
                value = response.get_json(silent=True)
                if value is None:
                    value = {'message': '接口返回非 JSON 内容', 'content_type': response.content_type}
                result = {'status': response.status_code, 'data': redact(value)}
        except Exception:
            raise RuntimeError('网站接口异常；操作结果需要核实，不自动重试。')
        if len(json.dumps(result)) > 32000:
            return {'status': response.status_code, 'error': '返回过大，请使用分页或缩小查询范围'}
        return result
