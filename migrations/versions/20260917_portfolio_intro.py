"""Store the portfolio introduction with the editable homepage profile."""
from alembic import op
import sqlalchemy as sa

revision = '20260917_portfolio_intro'
down_revision = '20260917_profile_site_name'
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name == 'mysql':
        op.execute('SET SESSION lock_wait_timeout = 10')
    op.add_column('personal_profiles', sa.Column('portfolio_introduction', sa.String(500), nullable=False, server_default=''))


def downgrade():
    with op.batch_alter_table('personal_profiles') as batch:
        batch.drop_column('portfolio_introduction')
