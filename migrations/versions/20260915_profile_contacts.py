"""Add contact-card fields to the singleton personal profile.

Revision ID: 20260915_profile_contacts
Revises: 20260915_personal_profile
"""
from alembic import op
import sqlalchemy as sa

revision = '20260915_profile_contacts'
down_revision = '20260915_personal_profile'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('personal_profiles', sa.Column('contact_email', sa.String(254), nullable=False, server_default=''))
    op.add_column('personal_profiles', sa.Column('wechat_id', sa.String(128), nullable=False, server_default=''))
    op.add_column('personal_profiles', sa.Column('wechat_qr_url', sa.String(2048), nullable=False, server_default=''))
    op.add_column('personal_profiles', sa.Column('contact_note', sa.String(500), nullable=False, server_default=''))


def downgrade():
    with op.batch_alter_table('personal_profiles') as batch:
        batch.drop_column('contact_note')
        batch.drop_column('wechat_qr_url')
        batch.drop_column('wechat_id')
        batch.drop_column('contact_email')
