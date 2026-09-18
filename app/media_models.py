"""Only files explicitly registered here are eligible for physical deletion."""
from datetime import datetime
from . import db


class MediaAsset(db.Model):
    __tablename__ = 'media_assets'
    id = db.Column(db.String(32), primary_key=True)
    storage_key = db.Column(db.String(255), nullable=False, unique=True)
    url = db.Column(db.String(1024), nullable=False)
    owner_id = db.Column(db.Integer, nullable=True)
    mime_type = db.Column(db.String(64), nullable=False)
    byte_size = db.Column(db.Integer, nullable=False)
    width = db.Column(db.Integer, nullable=False)
    height = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(16), nullable=False, default='uploading')
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    delete_after = db.Column(db.DateTime, nullable=True, index=True)
    delete_attempts = db.Column(db.Integer, nullable=False, default=0)


class MediaReference(db.Model):
    __tablename__ = 'media_references'
    asset_id = db.Column(db.String(32), db.ForeignKey('media_assets.id'), primary_key=True)
    content_type = db.Column(db.String(40), primary_key=True)
    content_id = db.Column(db.String(64), primary_key=True)
    __table_args__ = (db.Index('ix_media_reference_content', 'content_type', 'content_id'),)
