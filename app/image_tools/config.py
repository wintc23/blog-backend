import json
import re
from urllib.parse import urlsplit

RATIOS = ['auto', '1:1', '3:2', '2:3']
DEFAULTS = {
    'cartoon': dict(name='卡通画生成', description='把日常照片变成有趣的卡通画，人物、宠物和风景都可以。', mode='per_image', min_images=1, max_images=10, comparison=False, prompt_required=False,
        instruction='将输入照片转换为精致的卡通插画，保留主体身份、人数、动作及关键构图。区分场景内真实文字与后期叠加文字：重点还原人物衣服上的字样、印花，以及背景建筑、店铺招牌、路牌等物体上原本存在的文字，保留可辨认的文字内容、字体风格、排版、透视及与材质的融合关系，确保文字清晰、完整、易读，不出现错字、乱码或重影；无法辨认的内容不要编造。视频或截图后期叠加的字幕、解说文案、贴纸文字，以及抖音等平台水印、账号标识、点赞评论按钮等界面元素无需保留，应自然移除并补全其遮挡的场景；不要误删衣服、招牌、建筑等场景本身的文字。若原图中有人物，应在卡通化的同时细致还原每个人的面部特征，保留脸型、五官形状与比例、相对位置、神态和表情，保持人物辨识度，不套用统一脸型、不随意美化或改变年龄。细致还原手部动作，包括手势、手指数量与姿态、关节弯曲、手掌朝向，以及手与物体的接触和遮挡关系；保证五官、手部结构自然准确，避免五官错位、手指增减、粘连、扭曲或凭空补出被遮挡的肢体。不额外添加原图没有的文字、水印或无关人物。',
        fields=[dict(key='style', label='画风', options=['清新手绘', '日系动画', '立体卡通', '复古漫画'], default='清新手绘')]),
    'restore': dict(name='老照片修复', description='修复模糊、划痕和褪色，让珍贵记忆重新清晰。严重缺失的细节可能由 AI 推测补全。', mode='per_image', min_images=1, max_images=10, comparison=True, prompt_required=False,
        instruction='自然修复老照片的清晰度、噪点、划痕和褪色。保留人物身份、年龄、面部特征、服饰与年代感，不美颜、不改变构图、不虚构额外人物或物体。未要求上色时保留原有色彩。',
        fields=[dict(key='strength', label='修复程度', options=['自然修复', '加强修复'], default='自然修复'), dict(key='color', label='色彩', options=['保留原色', '黑白照片上色'], default='保留原色')]),
    'create': dict(name='自由生图', description='写下你的想法，让想象成为画面。也可以添加一张参考图片。', mode='reference', min_images=0, max_images=1, comparison=False, prompt_required=True,
        instruction='根据用户描述创作高质量图片。如果提供参考图，按照用户要求参考或编辑。', fields=[]),
}
for value in DEFAULTS.values():
    value.update(ratios=RATIOS, default_ratio='auto', max_outputs=4, default_count=1)


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def validate_config(value):
    if not isinstance(value, dict):
        raise ValueError('配置必须是对象')
    result = {}
    for name, limit in [('name', 60), ('description', 500), ('instruction', 8000)]:
        text = value.get(name)
        if not isinstance(text, str) or not text.strip() or len(text) > limit:
            raise ValueError('{} 长度不正确'.format(name))
        result[name] = text.strip()
    if value.get('mode') not in ('per_image', 'reference'):
        raise ValueError('请选择逐张转换或文字/参考图生成')
    result['mode'] = value['mode']
    cover = value.get('cover_url', '')
    if not isinstance(cover, str) or len(cover) > 1024:
        raise ValueError('封面地址不正确')
    if cover:
        parsed = urlsplit(cover)
        local_cover = cover.startswith('/images/tools/') and '..' not in cover and not parsed.query and not parsed.fragment
        if not local_cover and (parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.password):
            raise ValueError('封面请使用完整的 http 或 https 地址')
    result['cover_url'] = cover
    for name, default, low, high in [('min_images', 0, 0, 10), ('max_images', 1, 0, 10), ('max_outputs', 4, 1, 4)]:
        number = value.get(name, default)
        if type(number) is not int or not low <= number <= high:
            raise ValueError('图片数量设置不正确')
        result[name] = number
    count = value.get('default_count', 1)
    if type(count) is not int or not 1 <= count <= result['max_outputs']:
        raise ValueError('固定输出数量必须在允许范围内')
    result['default_count'] = count
    if result['min_images'] > result['max_images'] or result['mode'] == 'per_image' and result['min_images'] < 1:
        raise ValueError('输入图片数量范围不正确')
    # The initial provider adapter supports a single reference per generation.
    if result['mode'] == 'reference' and result['max_images'] > 1:
        raise ValueError('参考图生成目前最多支持一张参考图')
    ratios = value.get('ratios', RATIOS)
    if not isinstance(ratios, list) or not ratios or any(not isinstance(r, str) for r in ratios) or len(set(ratios)) != len(ratios) or any(r not in RATIOS for r in ratios):
        raise ValueError('画幅设置不正确')
    result['ratios'] = ratios
    result['default_ratio'] = value.get('default_ratio', ratios[0])
    if result['default_ratio'] not in ratios:
        raise ValueError('默认画幅不在可选范围')
    for name in ('comparison', 'prompt_required'):
        if type(value.get(name, False)) is not bool:
            raise ValueError('开关设置不正确')
        result[name] = value.get(name, False)
    fields = value.get('fields', [])
    if not isinstance(fields, list) or len(fields) > 8:
        raise ValueError('最多配置 8 个选项')
    result['fields'] = []
    keys = set()
    for field in fields:
        if not isinstance(field, dict):
            raise ValueError('选项格式不正确')
        key, label, options = field.get('key', ''), field.get('label', ''), field.get('options')
        if not isinstance(key, str) or not re.fullmatch('[a-z][a-z0-9_]{0,30}', key) or key in keys:
            raise ValueError('选项标识必须唯一，由小写字母、数字、下划线组成')
        if not isinstance(label, str) or not 1 <= len(label) <= 40 or not isinstance(options, list) or not 1 <= len(options) <= 12:
            raise ValueError('选项名称或数量不正确')
        if any(not isinstance(option, str) or not 1 <= len(option) <= 100 for option in options):
            raise ValueError('选项内容不正确')
        default = field.get('default', options[0])
        if default not in options:
            raise ValueError('默认值必须属于选项')
        keys.add(key)
        result['fields'].append(dict(key=key, label=label, options=options, default=default))
    return result


def public_config(config, slug=None):
    result = {key: value for key, value in config.items() if key != 'instruction'}
    if not result.get('cover_url') and slug:
        from flask import current_app
        cover = slug if slug in ('cartoon', 'restore', 'create') else 'create'
        result['cover_url'] = current_app.config['QI_NIU_LINK_URL'].rstrip('/') + '/image-tool-covers/' + cover + '.webp'
    return result


def validate_options(config, data):
    if not isinstance(data, dict):
        raise ValueError('参数不正确')
    prompt = data.get('prompt', '')
    if not isinstance(prompt, str) or len(prompt) > 4000 or config['prompt_required'] and not prompt.strip():
        raise ValueError('请填写生成要求，最多 4000 字')
    # Template settings are defaults; advanced overrides stay inside its allowed values.
    ratio = data.get('ratio', config['default_ratio'])
    if ratio not in config['ratios']:
        raise ValueError('请选择允许的输出画幅')
    count = 1 if config['mode'] == 'per_image' else config.get('default_count', 1)
    supplied = data.get('fields', {})
    if not isinstance(supplied, dict):
        raise ValueError('高级参数不正确')
    fields = {}
    for field in config['fields']:
        value = supplied.get(field['key'], field['default'])
        if value not in field['options']:
            raise ValueError('请选择允许的{}选项'.format(field['label']))
        fields[field['key']] = value
    if set(supplied) - set(fields):
        raise ValueError('存在未知的高级参数')
    return dict(prompt=prompt.strip(), ratio=ratio, count=count, fields=fields)
