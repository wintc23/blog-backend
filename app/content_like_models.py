from datetime import datetime
from . import db


class ContentLike(db.Model):
    __tablename__ = 'content_likes'
    kind = db.Column(db.String(16), primary_key=True)
    target_id = db.Column(db.String(64), primary_key=True)
    author_id = db.Column(db.Integer, db.ForeignKey('users.id'), primary_key=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
