"""Likes for life moments, image tools and active image shares."""
from alembic import op
import sqlalchemy as sa
revision = '20260919_content_likes'
down_revision = '20260919_albums_device_login'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('content_likes',
        sa.Column('kind', sa.String(16), primary_key=True),
        sa.Column('target_id', sa.String(64), primary_key=True),
        sa.Column('author_id', sa.Integer(), sa.ForeignKey('users.id'), primary_key=True),
        sa.Column('created_at', sa.DateTime(), nullable=False))


def downgrade():
    op.drop_table('content_likes')
