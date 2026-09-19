from datetime import datetime
from . import db


class Album(db.Model):
    __tablename__ = 'albums'
    id = db.Column(db.String(32), primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    title = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(1000), nullable=False, default='')
    visibility = db.Column(db.String(12), nullable=False, default='private', index=True)
    cover_id = db.Column(db.String(32), nullable=True)
    version = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class AlbumPhoto(db.Model):
    __tablename__ = 'album_photos'
    id = db.Column(db.String(32), primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    source_key = db.Column(db.String(64), nullable=False)
    source_kind = db.Column(db.String(16), nullable=False)
    url = db.Column(db.Text, nullable=True)
    cloud_key = db.Column(db.String(255), nullable=True)
    name = db.Column(db.String(180), nullable=False, default='图片')
    width = db.Column(db.Integer, nullable=False, default=0)
    height = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint('owner_id', 'source_key', name='uq_album_photo_owner_source'),)


class AlbumItem(db.Model):
    __tablename__ = 'album_items'
    album_id = db.Column(db.String(32), db.ForeignKey('albums.id'), primary_key=True)
    photo_id = db.Column(db.String(32), db.ForeignKey('album_photos.id'), primary_key=True)
    position = db.Column(db.Integer, nullable=False, default=0)
    added_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class DeviceLogin(db.Model):
    __tablename__ = 'device_logins'
    id = db.Column(db.String(32), primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    auth_version = db.Column(db.Integer, nullable=False)
    scan_hash = db.Column(db.String(64), nullable=False)
    claim_hash = db.Column(db.String(64), nullable=True)
    code = db.Column(db.String(6), nullable=True)
    status = db.Column(db.String(16), nullable=False, default='waiting')
    expires_at = db.Column(db.DateTime, nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
