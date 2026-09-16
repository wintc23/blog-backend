import copy
import json
import re
from urllib.parse import urlsplit
from datetime import datetime, timedelta
from dateutil import tz


class GenerationError(Exception):
    def __init__(self, code, message, retryable=False):
        super().__init__(message)
        self.code, self.retryable = code, retryable


class LeaseLost(GenerationError):
    def __init__(self):
        super().__init__('lease_lost', '任务已被取消或由其他进程接管')


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def utcnow():
    return datetime.utcnow()


def iso(value):
    return value.isoformat() + 'Z' if value else None


DIGEST_GROUPS = [
    {'id': 'applications', 'title': '应用与工具', 'description': '新工具、新功能，以及它们能帮你完成什么。'},
    {'id': 'development', 'title': '技术与开发', 'description': '模型、API、开源与工程实践，关注能力和使用边界。'},
]

EDITORIAL_PROMPT = '''每天整理一篇中文 AI 行业动态，面向普通 AI 用户与开发者。
先写 2–3 条本期速览，摘要涵盖当期实际存在的两个分区重点。
应用与工具：优先 2–3 条，用直白语言说明更新、适用对象、用途、体验入口、费用与开放限制；只写来源能够证实的条件，影响实际使用决策但尚未公布的条件简短说明。
技术与开发：优先 1–3 条，关注模型、API、成本、开源许可、部署与集成条件，保留指标的评测条件和边界。
同一事件只介绍一次，按主要价值归类，跨受众的影响作为该条补充。术语首次出现时解释实际意义。
有价值的消息不足时减少条数，允许某个分区为空，不凑数、不编造实测经历。不把采集时间当作新闻发布时间，旧消息标注原始日期。
同时关注中国与海外 AI 动态，国内范围包括模型与开源、产品应用、企业服务、政策与产业；按读者价值、重要性和证据质量选择，不设置国内配额，不因地域硬凑条目。
区分产品首次上线、后续升级和发布会：同一产品的新能力发布是独立事件，应核对发生日期，不能用首次上线日期排除后续更新。
优先采用可核查的一手资料；可信媒体的现场报道须注明报道方，不能冒充官方公告，传闻与未经核实的营销说法不收录。保留出处；事实和简析分开，简析没有具体价值时留空。全文简洁，目标 800–1500 字。
写给读者看，不使用“给定资料”“分区留空”等生成过程用语，不反复罗列资料未谈及的价格、许可、评测等事项。'''


def validate_digest_groups(groups):
    if not isinstance(groups, list) or len(groups) != 2:
        raise ValueError('请配置两个阅读分区')
    ids = []
    for group in groups:
        if not isinstance(group, dict) or set(group) != {'id', 'title', 'description'}:
            raise ValueError('阅读分区字段不完整')
        ids.append(group['id'])
        for key, limit in [('title', 32), ('description', 200)]:
            value = group[key]
            if not isinstance(value, str) or not value.strip() or len(value) > limit or re.search(r'<[^>]+>', value):
                raise ValueError('分区名称或说明为空、过长或包含 HTML')
    if ids != ['applications', 'development']:
        raise ValueError('阅读分区须依次为 applications、development')
    return groups


def default_config():
    return {
        'timezone': 'Asia/Shanghai', 'generate_time': '08:30', 'publish_time': '09:00',
        'late_minutes': 180, 'max_retries': 3, 'auto_publish': False,
        'source_ids': [], 'lookback_hours': 24, 'max_lookback_hours': 72,
        'min_chars': 300, 'max_chars': 1500, 'max_items': 16,
        'digest_groups': copy.deepcopy(DIGEST_GROUPS),
        'prompt': EDITORIAL_PROMPT,
        'image_prompt': '蓝白主色，明亮、清晰、有活力的横向编辑插图。围绕本期主要事件选用独特构图，不重复近期封面，不写文字、日期或商标。',
        'text_model': {'provider': 'openai_compatible', 'base_url': '', 'model': '', 'credential_ref': 'CONTENT_TEXT_API_KEY', 'timeout': 180},
        'image_model': {'base_url': '', 'model': '', 'credential_ref': 'CONTENT_IMAGE_API_KEY', 'timeout': 300,
                        'size': '1536x1024', 'response_format': 'auto'},
    }


def validate_config(raw):
    if not isinstance(raw, dict) or set(raw) - set(default_config()):
        raise ValueError('任务配置字段不正确')
    result = default_config()
    result.update(copy.deepcopy(raw))
    validate_digest_groups(result['digest_groups'])
    if result['timezone'] != 'Asia/Shanghai':
        raise ValueError('当前动态使用 Asia/Shanghai 时区')
    for key in ('generate_time', 'publish_time'):
        if not isinstance(result[key], str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', result[key]):
            raise ValueError('时间必须为 HH:MM')
    if result['generate_time'] >= result['publish_time']:
        raise ValueError('生成时间必须早于发布时间')
    for key, low, high in [('late_minutes', 0, 720), ('max_retries', 0, 3), ('lookback_hours', 1, 72),
                           ('max_lookback_hours', 1, 168), ('min_chars', 100, 10000),
                           ('max_chars', 100, 15000), ('max_items', 1, 30)]:
        if type(result[key]) is not int or not low <= result[key] <= high:
            raise ValueError('{} 超出范围 {}–{}'.format(key, low, high))
    if result['min_chars'] > result['max_chars'] or result['lookback_hours'] > result['max_lookback_hours']:
        raise ValueError('最小范围不能超过最大范围')
    if type(result['auto_publish']) is not bool:
        raise ValueError('自动发布必须为布尔值')
    ids = result['source_ids']
    if not isinstance(ids, list) or len(ids) > 30 or any(type(i) is not int or i < 1 for i in ids):
        raise ValueError('来源列表不正确')
    result['source_ids'] = list(dict.fromkeys(ids))
    for key in ('prompt', 'image_prompt'):
        if not isinstance(result[key], str) or not 1 <= len(result[key].strip()) <= 10000:
            raise ValueError('提示词不能为空，且最多 10000 字')
    for key in ('text_model', 'image_model'):
        model = result[key]
        if key == 'text_model' and isinstance(model, dict):
            model = dict({'provider': 'openai_compatible'}, **model)
            result[key] = model
        allowed = set(default_config()[key])
        if not isinstance(model, dict) or set(model) != allowed:
            raise ValueError('模型配置字段不完整')
        if key == 'text_model' and model['provider'] not in ('openai_compatible', 'codex'):
            raise ValueError('文字生成方式不正确')
        if not all(isinstance(model[k], str) and len(model[k]) <= 500 for k in ('base_url', 'model', 'credential_ref')):
            raise ValueError('模型配置不正确')
        if not re.fullmatch(r'CONTENT_[A-Z0-9_]+_KEY', model['credential_ref']):
            raise ValueError('凭据引用必须为 CONTENT_ 开头、_KEY 结尾的环境变量名')
        if type(model['timeout']) is not int or not 30 <= model['timeout'] <= 600:
            raise ValueError('模型超时应在 30–600 秒之间')
        if model['base_url']:
            from .network import validate_url
            validate_url(model['base_url'], resolve=False)
            if urlsplit(model['base_url']).query:
                raise ValueError('模型 API 地址不能包含查询参数；密钥请使用服务端凭据引用')
    image = result['image_model']
    if image['response_format'] not in ('auto', 'b64_json') or not re.fullmatch(r'\d{3,4}x\d{3,4}|auto', image['size']):
        raise ValueError('图片尺寸或返回格式不正确')
    return result


def schedule_for(edition, config):
    zone = tz.gettz(config['timezone'])
    def at(clock):
        local = datetime.combine(edition, datetime.strptime(clock, '%H:%M').time()).replace(tzinfo=zone)
        return local.astimezone(tz.UTC).replace(tzinfo=None)
    publish = at(config['publish_time'])
    return at(config['generate_time']), publish, publish + timedelta(minutes=config['late_minutes'])


def local_day(now, config):
    return now.replace(tzinfo=tz.UTC).astimezone(tz.gettz(config['timezone'])).date()
