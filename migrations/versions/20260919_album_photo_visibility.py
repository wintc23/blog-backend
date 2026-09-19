"""Independent photo visibility within each album; existing photos stay public."""
from alembic import op
import sqlalchemy as sa
revision = '20260919_album_photo_visibility'
down_revision = '20260919_interaction_notify'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('album_items', sa.Column('is_public', sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade():
    op.drop_column('album_items', 'is_public')
