"""Private, durable image tools. Public tool configurations never contain prompts."""
from datetime import datetime
from . import db


class ImageTool(db.Model):
    __tablename__ = 'image_tools'
    slug = db.Column(db.String(64), primary_key=True)
    version = db.Column(db.Integer, nullable=False, default=1)
    enabled = db.Column(db.Boolean, nullable=False, default=False)
    position = db.Column(db.Integer, nullable=False, default=0)
    config_json = db.Column(db.Text, nullable=False)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class ImageTask(db.Model):
    __tablename__ = 'image_tool_tasks'
    id = db.Column(db.String(32), primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    tool_slug = db.Column(db.String(64), nullable=False)
    tool_version = db.Column(db.Integer, nullable=False)
    snapshot_json = db.Column(db.Text, nullable=False)
    options_json = db.Column(db.Text, nullable=False, default='{}')
    status = db.Column(db.String(16), nullable=False, default='draft')
    upload_nonce = db.Column(db.String(32), nullable=True)
    share_hash = db.Column(db.String(64), nullable=True, unique=True)
    share_assets_json = db.Column(db.Text, nullable=True)
    share_until = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    deleted_at = db.Column(db.DateTime, nullable=True)


class ImageToolAsset(db.Model):
    __tablename__ = 'image_tool_assets'
    id = db.Column(db.String(32), primary_key=True)
    task_id = db.Column(db.String(32), db.ForeignKey('image_tool_tasks.id'), nullable=False, index=True)
    kind = db.Column(db.String(12), nullable=False)
    name = db.Column(db.String(180), nullable=False)
    extension = db.Column(db.String(8), nullable=False)
    width = db.Column(db.Integer, nullable=False)
    height = db.Column(db.Integer, nullable=False)
    byte_size = db.Column(db.Integer, nullable=False)
    position = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class ImageToolItem(db.Model):
    __tablename__ = 'image_tool_items'
    id = db.Column(db.String(32), primary_key=True)
    task_id = db.Column(db.String(32), db.ForeignKey('image_tool_tasks.id'), nullable=False, index=True)
    source_id = db.Column(db.String(32), nullable=True)
    output_id = db.Column(db.String(32), nullable=True)
    previous_id = db.Column(db.String(32), nullable=True)
    position = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(16), nullable=False, default='queued', index=True)
    error = db.Column(db.String(300), nullable=True)
    quota_exempt = db.Column(db.Boolean, nullable=False, default=False)
    started_at = db.Column(db.DateTime, nullable=True, index=True)
    lease_token = db.Column(db.String(32), nullable=True)
    lease_until = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    finished_at = db.Column(db.DateTime, nullable=True)


class ImageToolSettings(db.Model):
    __tablename__ = 'image_tool_settings'
    id = db.Column(db.Integer, primary_key=True)
    version = db.Column(db.Integer, nullable=False, default=1)
    global_per_minute = db.Column(db.Integer, nullable=False, default=10)
    user_per_hour = db.Column(db.Integer, nullable=False, default=10)
    upload_max_mb = db.Column(db.Integer, nullable=False, default=20)
    upload_max_megapixels = db.Column(db.Integer, nullable=False, default=40)
    processing_max_edge = db.Column(db.Integer, nullable=False, default=2048)
