"""ai chat

Revision ID: 20260704_ai_chat
Revises: 8b6141921bce
Create Date: 2026-07-04 18:20:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision = '20260704_ai_chat'
down_revision = '8b6141921bce'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'ai_access_keys',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('key_hash', sa.String(length=64), nullable=False),
        sa.Column('key_preview', sa.String(length=32), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=True),
        sa.Column('usage_limit', sa.Integer(), nullable=True),
        sa.Column('usage_count', sa.Integer(), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('last_used_at', sa.DateTime(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ai_access_keys_enabled'), 'ai_access_keys', ['enabled'], unique=False)
    op.create_index(op.f('ix_ai_access_keys_key_hash'), 'ai_access_keys', ['key_hash'], unique=True)

    op.create_table(
        'ai_chat_sessions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('access_key_id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=128), nullable=True),
        sa.Column('codex_session_id', sa.String(length=128), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('last_message_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['access_key_id'], ['ai_access_keys.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ai_chat_sessions_access_key_id'), 'ai_chat_sessions', ['access_key_id'], unique=False)
    op.create_index(op.f('ix_ai_chat_sessions_codex_session_id'), 'ai_chat_sessions', ['codex_session_id'], unique=False)
    op.create_index(op.f('ix_ai_chat_sessions_status'), 'ai_chat_sessions', ['status'], unique=False)

    op.create_table(
        'ai_chat_messages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('session_id', sa.Integer(), nullable=False),
        sa.Column('role', sa.String(length=16), nullable=False),
        sa.Column('content', mysql.MEDIUMTEXT(), nullable=True),
        sa.Column('content_type', sa.String(length=16), nullable=True),
        sa.Column('codex_message_id', sa.String(length=128), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=True),
        sa.Column('metadata_json', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['session_id'], ['ai_chat_sessions.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ai_chat_messages_created_at'), 'ai_chat_messages', ['created_at'], unique=False)
    op.create_index(op.f('ix_ai_chat_messages_session_id'), 'ai_chat_messages', ['session_id'], unique=False)

    op.create_table(
        'ai_chat_attachments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('message_id', sa.Integer(), nullable=False),
        sa.Column('file_key', sa.String(length=255), nullable=False),
        sa.Column('file_url', sa.Text(), nullable=True),
        sa.Column('mime_type', sa.String(length=128), nullable=True),
        sa.Column('size', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['message_id'], ['ai_chat_messages.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ai_chat_attachments_message_id'), 'ai_chat_attachments', ['message_id'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_ai_chat_attachments_message_id'), table_name='ai_chat_attachments')
    op.drop_table('ai_chat_attachments')
    op.drop_index(op.f('ix_ai_chat_messages_session_id'), table_name='ai_chat_messages')
    op.drop_index(op.f('ix_ai_chat_messages_created_at'), table_name='ai_chat_messages')
    op.drop_table('ai_chat_messages')
    op.drop_index(op.f('ix_ai_chat_sessions_status'), table_name='ai_chat_sessions')
    op.drop_index(op.f('ix_ai_chat_sessions_codex_session_id'), table_name='ai_chat_sessions')
    op.drop_index(op.f('ix_ai_chat_sessions_access_key_id'), table_name='ai_chat_sessions')
    op.drop_table('ai_chat_sessions')
    op.drop_index(op.f('ix_ai_access_keys_key_hash'), table_name='ai_access_keys')
    op.drop_index(op.f('ix_ai_access_keys_enabled'), table_name='ai_access_keys')
    op.drop_table('ai_access_keys')
