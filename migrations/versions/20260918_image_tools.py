"""Private image tools, version snapshots, assets and durable execution items."""
from alembic import op
import sqlalchemy as sa

revision = '20260918_image_tools'
down_revision = '20260918_digest_likes'
branch_labels = None
depends_on = None


def upgrade():
    table = op.create_table('image_tool_settings',
        sa.Column('id', sa.Integer(), primary_key=True), sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('global_per_minute', sa.Integer(), nullable=False), sa.Column('user_per_hour', sa.Integer(), nullable=False))
    op.bulk_insert(table, [dict(id=1, version=1, global_per_minute=10, user_per_hour=10)])
    op.create_table('image_tools',
        sa.Column('slug', sa.String(64), primary_key=True), sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False), sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('config_json', sa.Text(), nullable=False), sa.Column('updated_at', sa.DateTime(), nullable=False))
    op.create_table('image_tool_tasks',
        sa.Column('id', sa.String(32), primary_key=True), sa.Column('owner_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('tool_slug', sa.String(64), nullable=False), sa.Column('tool_version', sa.Integer(), nullable=False),
        sa.Column('snapshot_json', sa.Text(), nullable=False), sa.Column('options_json', sa.Text(), nullable=False),
        sa.Column('status', sa.String(16), nullable=False), sa.Column('upload_nonce', sa.String(32)),
        sa.Column('share_hash', sa.String(64), unique=True), sa.Column('share_assets_json', sa.Text()), sa.Column('share_until', sa.DateTime()),
        sa.Column('created_at', sa.DateTime(), nullable=False), sa.Column('updated_at', sa.DateTime(), nullable=False), sa.Column('deleted_at', sa.DateTime()))
    op.create_index('ix_image_tool_tasks_owner_id', 'image_tool_tasks', ['owner_id'])
    op.create_table('image_tool_assets',
        sa.Column('id', sa.String(32), primary_key=True), sa.Column('task_id', sa.String(32), sa.ForeignKey('image_tool_tasks.id'), nullable=False),
        sa.Column('kind', sa.String(12), nullable=False), sa.Column('name', sa.String(180), nullable=False),
        sa.Column('extension', sa.String(8), nullable=False), sa.Column('width', sa.Integer(), nullable=False), sa.Column('height', sa.Integer(), nullable=False),
        sa.Column('byte_size', sa.Integer(), nullable=False), sa.Column('position', sa.Integer(), nullable=False), sa.Column('created_at', sa.DateTime(), nullable=False))
    op.create_index('ix_image_tool_assets_task_id', 'image_tool_assets', ['task_id'])
    op.create_table('image_tool_items',
        sa.Column('id', sa.String(32), primary_key=True), sa.Column('task_id', sa.String(32), sa.ForeignKey('image_tool_tasks.id'), nullable=False),
        sa.Column('source_id', sa.String(32)), sa.Column('output_id', sa.String(32)), sa.Column('previous_id', sa.String(32)),
        sa.Column('position', sa.Integer(), nullable=False), sa.Column('status', sa.String(16), nullable=False), sa.Column('error', sa.String(300)),
        sa.Column('quota_exempt', sa.Boolean(), nullable=False), sa.Column('started_at', sa.DateTime()),
        sa.Column('lease_token', sa.String(32)), sa.Column('lease_until', sa.DateTime()), sa.Column('created_at', sa.DateTime(), nullable=False), sa.Column('finished_at', sa.DateTime()))
    op.create_index('ix_image_tool_items_task_id', 'image_tool_items', ['task_id'])
    op.create_index('ix_image_tool_items_started_at', 'image_tool_items', ['started_at'])
    op.create_index('ix_image_tool_items_status', 'image_tool_items', ['status'])


def downgrade():
    for table in ['image_tool_items', 'image_tool_assets', 'image_tool_tasks', 'image_tools', 'image_tool_settings']:
        op.drop_table(table)
