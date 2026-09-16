"""Store the public site name with the editable personal profile."""
from alembic import op
import sqlalchemy as sa

revision = '20260917_profile_site_name'
down_revision = '20260916_digest_reads'
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name == 'mysql':
        op.execute('SET SESSION lock_wait_timeout = 10')
    op.add_column('personal_profiles', sa.Column('site_name', sa.String(128), nullable=False, server_default=''))


def downgrade():
    with op.batch_alter_table('personal_profiles') as batch:
        batch.drop_column('site_name')
