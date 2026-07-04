"""ai session pinned

Revision ID: 20260704_ai_session_pinned
Revises: 20260704_ai_key_encrypted
Create Date: 2026-07-04 19:50:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = '20260704_ai_session_pinned'
down_revision = '20260704_ai_key_encrypted'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('ai_chat_sessions', sa.Column('pinned', sa.Boolean(), nullable=True))
    op.create_index(op.f('ix_ai_chat_sessions_pinned'), 'ai_chat_sessions', ['pinned'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_ai_chat_sessions_pinned'), table_name='ai_chat_sessions')
    op.drop_column('ai_chat_sessions', 'pinned')
