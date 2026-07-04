"""ai key encrypted

Revision ID: 20260704_ai_key_encrypted
Revises: 20260704_ai_chat
Create Date: 2026-07-04 19:10:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = '20260704_ai_key_encrypted'
down_revision = '20260704_ai_chat'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('ai_access_keys', sa.Column('key_encrypted', sa.Text(), nullable=True))


def downgrade():
    op.drop_column('ai_access_keys', 'key_encrypted')
