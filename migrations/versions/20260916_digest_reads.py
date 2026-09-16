"""Persist AI news read counts independently of content revisions."""
from alembic import op
import sqlalchemy as sa

revision = '20260916_digest_reads'
down_revision = '20260916_life_moments'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('ai_digests', sa.Column('read_times', sa.BigInteger(), nullable=False, server_default='0'))


def downgrade():
    with op.batch_alter_table('ai_digests') as batch:
        batch.drop_column('read_times')
