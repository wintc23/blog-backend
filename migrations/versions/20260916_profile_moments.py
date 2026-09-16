"""Add recent life moments to the homepage profile."""
from alembic import op
import sqlalchemy as sa

revision = '20260916_profile_moments'
down_revision = '20260916_generation'
branch_labels = None
depends_on = None


def upgrade():
    # Nullable TEXT works on MySQL versions that do not support TEXT defaults.
    op.add_column('personal_profiles', sa.Column('moments_json', sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table('personal_profiles') as batch:
        batch.drop_column('moments_json')
