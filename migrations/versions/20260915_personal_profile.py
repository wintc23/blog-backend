"""Add independently managed homepage personal information.

Revision ID: 20260915_personal_profile
Revises: 20260726_product_sections
"""
from alembic import op
import sqlalchemy as sa


revision = '20260915_personal_profile'
down_revision = '20260726_product_sections'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'personal_profiles',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=False),
        sa.Column('display_name', sa.String(128), nullable=False),
        sa.Column('avatar_url', sa.Text(), nullable=False),
        sa.Column('tagline', sa.String(255), nullable=False),
        sa.Column('introduction', sa.String(500), nullable=False),
        sa.Column('bio', sa.Text(), nullable=False),
        sa.Column('links_json', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade():
    op.drop_table('personal_profiles')
