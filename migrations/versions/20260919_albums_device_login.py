"""Reusable photo collections and approved one-time device login."""
from alembic import op
import sqlalchemy as sa
revision = '20260919_albums_device_login'
down_revision = '20260919_image_upload_policy'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('albums', sa.Column('id', sa.String(32), primary_key=True), sa.Column('owner_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('title', sa.String(100), nullable=False), sa.Column('description', sa.String(1000), nullable=False), sa.Column('visibility', sa.String(12), nullable=False),
        sa.Column('cover_id', sa.String(32)), sa.Column('version', sa.Integer(), nullable=False), sa.Column('created_at', sa.DateTime(), nullable=False), sa.Column('updated_at', sa.DateTime(), nullable=False))
    op.create_index('ix_albums_owner_id', 'albums', ['owner_id'])
    op.create_index('ix_albums_visibility', 'albums', ['visibility'])
    op.create_table('album_photos', sa.Column('id', sa.String(32), primary_key=True), sa.Column('owner_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('source_key', sa.String(64), nullable=False), sa.Column('source_kind', sa.String(16), nullable=False), sa.Column('url', sa.Text()), sa.Column('cloud_key', sa.String(255)),
        sa.Column('name', sa.String(180), nullable=False), sa.Column('width', sa.Integer(), nullable=False), sa.Column('height', sa.Integer(), nullable=False), sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('owner_id', 'source_key', name='uq_album_photo_owner_source'))
    op.create_index('ix_album_photos_owner_id', 'album_photos', ['owner_id'])
    op.create_table('album_items', sa.Column('album_id', sa.String(32), sa.ForeignKey('albums.id'), primary_key=True), sa.Column('photo_id', sa.String(32), sa.ForeignKey('album_photos.id'), primary_key=True),
        sa.Column('position', sa.Integer(), nullable=False), sa.Column('added_at', sa.DateTime(), nullable=False))
    op.create_table('device_logins', sa.Column('id', sa.String(32), primary_key=True), sa.Column('owner_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('auth_version', sa.Integer(), nullable=False), sa.Column('scan_hash', sa.String(64), nullable=False), sa.Column('claim_hash', sa.String(64)), sa.Column('code', sa.String(6)),
        sa.Column('status', sa.String(16), nullable=False), sa.Column('expires_at', sa.DateTime(), nullable=False), sa.Column('created_at', sa.DateTime(), nullable=False))
    op.create_index('ix_device_logins_owner_id', 'device_logins', ['owner_id'])
    op.create_index('ix_device_logins_expires_at', 'device_logins', ['expires_at'])


def downgrade():
    for name in ('device_logins', 'album_items', 'album_photos', 'albums'):
        op.drop_table(name)
