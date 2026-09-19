"""Durable per-channel owner interaction notifications."""
from alembic import op
import sqlalchemy as sa
revision = '20260919_interaction_notify'
down_revision = '20260919_content_likes'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('interaction_notifications',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('channel', sa.String(8), nullable=False),
        sa.Column('title', sa.String(80), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('path', sa.String(512), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('available_at', sa.DateTime(), nullable=False),
        sa.Column('sent_at', sa.DateTime()),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('lease', sa.String(32)),
        sa.Column('last_error', sa.String(80)))
    op.create_index('ix_interaction_notifications_available_at', 'interaction_notifications', ['available_at'])


def downgrade():
    op.drop_table('interaction_notifications')
