"""add portfolio products

Revision ID: 20260726_products
Revises: 20260704_ai_session_pinned
Create Date: 2026-07-26 17:30:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision = '20260726_products'
down_revision = '20260704_ai_session_pinned'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'products',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('slug', sa.String(length=128), nullable=False),
        sa.Column('tagline', sa.String(length=255), nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('platform', sa.String(length=64), nullable=True),
        sa.Column('version', sa.String(length=32), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=True),
        sa.Column('status_label', sa.String(length=64), nullable=True),
        sa.Column('logo_url', sa.Text(), nullable=True),
        sa.Column('cover_url', sa.Text(), nullable=True),
        sa.Column('accent_color', sa.String(length=16), nullable=True),
        sa.Column('highlights_json', sa.Text(), nullable=True),
        sa.Column('features_json', mysql.MEDIUMTEXT(), nullable=True),
        sa.Column('steps_json', mysql.MEDIUMTEXT(), nullable=True),
        sa.Column('screenshots_json', mysql.MEDIUMTEXT(), nullable=True),
        sa.Column('links_json', sa.Text(), nullable=True),
        sa.Column('story_html', mysql.MEDIUMTEXT(), nullable=True),
        sa.Column('published', sa.Boolean(), nullable=True),
        sa.Column('featured', sa.Boolean(), nullable=True),
        sa.Column('sort', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_products_featured'), 'products', ['featured'], unique=False)
    op.create_index(op.f('ix_products_published'), 'products', ['published'], unique=False)
    op.create_index(op.f('ix_products_slug'), 'products', ['slug'], unique=True)
    op.create_index(op.f('ix_products_sort'), 'products', ['sort'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_products_sort'), table_name='products')
    op.drop_index(op.f('ix_products_slug'), table_name='products')
    op.drop_index(op.f('ix_products_published'), table_name='products')
    op.drop_index(op.f('ix_products_featured'), table_name='products')
    op.drop_table('products')
