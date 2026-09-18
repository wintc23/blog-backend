"""First-party operational events: never include pictures, names, prompts or URLs."""
import json
from .. import db
from ..models import StatEvent


def record(event, task, **values):
    allowed = {key: value for key, value in values.items() if key in ('count', 'status', 'duration_ms', 'source')}
    allowed['tool'] = task.tool_slug
    db.session.add(StatEvent(name='image_tool.' + event, author_id=task.owner_id,
                            params=json.dumps(allowed, ensure_ascii=False)))
