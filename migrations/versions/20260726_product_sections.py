"""add customizable product sections

Revision ID: 20260726_product_sections
Revises: 20260726_products
Create Date: 2026-07-26 18:20:00.000000

"""
import json

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision = '20260726_product_sections'
down_revision = '20260726_products'
branch_labels = None
depends_on = None


def _parse_list(value):
    if not value:
        return []
    try:
        data = json.loads(value)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def upgrade():
    op.create_table(
        'product_sections',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('product_id', sa.Integer(), nullable=False),
        sa.Column('type', sa.String(length=32), nullable=False),
        sa.Column('title', sa.String(length=128), nullable=True),
        sa.Column('subtitle', sa.String(length=128), nullable=True),
        sa.Column('layout', sa.String(length=32), nullable=True),
        sa.Column('content_json', mysql.MEDIUMTEXT(), nullable=True),
        sa.Column('visible', sa.Boolean(), nullable=True),
        sa.Column('sort', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['product_id'], ['products.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_product_sections_product_id'), 'product_sections', ['product_id'], unique=False)
    op.create_index(op.f('ix_product_sections_sort'), 'product_sections', ['sort'], unique=False)

    bind = op.get_bind()
    products = bind.execute(sa.text(
        'SELECT id, features_json, steps_json, screenshots_json, story_html FROM products'
    )).fetchall()
    insert = sa.text(
        'INSERT INTO product_sections '
        '(product_id, type, title, subtitle, layout, content_json, visible, sort, created_at, updated_at) '
        'VALUES (:product_id, :type, :title, :subtitle, :layout, :content_json, 1, :sort, NOW(), NOW())'
    )
    for product in products:
        sections = []
        features = _parse_list(product.features_json)
        steps = _parse_list(product.steps_json)
        screenshots = _parse_list(product.screenshots_json)
        if features:
            sections.append(('feature_grid', '核心能力', 'CAPABILITIES', 'columns-2', {'items': features}))
        if steps:
            sections.append(('steps', '如何使用', '', 'columns-3', {'items': steps}))
        if screenshots:
            sections.append(('gallery', '产品截图', 'GALLERY', 'carousel', {'items': screenshots}))
        if product.story_html:
            sections.append(('rich_text', '为什么做这个产品', '', 'default', {'html': product.story_html}))
        for index, section in enumerate(sections):
            bind.execute(insert, {
                'product_id': product.id,
                'type': section[0],
                'title': section[1],
                'subtitle': section[2],
                'layout': section[3],
                'content_json': json.dumps(section[4], ensure_ascii=False),
                'sort': index,
            })


def downgrade():
    op.drop_index(op.f('ix_product_sections_sort'), table_name='product_sections')
    op.drop_index(op.f('ix_product_sections_product_id'), table_name='product_sections')
    op.drop_table('product_sections')
