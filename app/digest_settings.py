"""Public channel copy, stored separately from generation instructions."""
import json
from .generation.configuration import validate_digest_groups


def validate_introduction(value):
    if not isinstance(value, dict):
        raise ValueError('请填写栏目介绍')
    result = {}
    for key, label, limit in [('summary', '栏目说明', 200), ('note', '来源说明', 120)]:
        text = value.get(key, '')
        if not isinstance(text, str) or len(text) > limit or (key == 'summary' and not text.strip()):
            raise ValueError('{}须为 {} 字以内的文本'.format(label, limit))
        result[key] = text.strip()
    result['groups'] = [dict(group) for group in validate_digest_groups(value.get('groups'))]
    return result


def public_settings(settings):
    if not settings:
        return None
    introduction = json.loads(settings.preferences_json).get('introduction')
    return {'title': settings.title, 'timezone': settings.timezone,
            'publish_time': settings.publish_time.strftime('%H:%M'),
            'introduction': validate_introduction(introduction) if introduction else None}
