"""Configurable upload retention and model-input dimensions."""
from alembic import op
import sqlalchemy as sa
revision = '20260919_image_upload_policy'
down_revision = '20260918_image_tools'
branch_labels = None
depends_on = None


def upgrade():
    for name, value in [('upload_max_mb', '20'), ('upload_max_megapixels', '40'), ('processing_max_edge', '2048')]:
        op.add_column('image_tool_settings', sa.Column(name, sa.Integer(), nullable=False, server_default=value))


def downgrade():
    for name in ('processing_max_edge', 'upload_max_megapixels', 'upload_max_mb'):
        op.drop_column('image_tool_settings', name)
