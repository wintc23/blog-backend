"""Persistent likes for published AI digests, one per user and issue."""
from alembic import op
import sqlalchemy as sa

revision = '20260918_digest_likes'
down_revision = '20260917_moment_gallery'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('ai_digest_likes',
        sa.Column('digest_id', sa.Integer(), sa.ForeignKey('ai_digests.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('author_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('timestamp', sa.DateTime(), nullable=False))
    op.create_index('ix_ai_digest_likes_author_id', 'ai_digest_likes', ['author_id'])


def downgrade():
    op.drop_table('ai_digest_likes')
