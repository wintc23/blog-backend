"""Email sign-in challenges and revocable user sessions."""
from alembic import op
import sqlalchemy as sa

revision = '20260917_email_login'
down_revision = '20260917_guest_login'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('email_verified_at', sa.DateTime(), nullable=True))
    op.add_column('users', sa.Column('email_version', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('users', sa.Column('auth_version', sa.Integer(), nullable=False, server_default='0'))
    op.create_table('email_login_challenges',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('email', sa.String(64), nullable=False),
        sa.Column('code_digest', sa.String(64), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('email_version', sa.Integer(), nullable=False),
        sa.Column('expires_at', sa.BigInteger(), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('ready', sa.Boolean(), nullable=False),
        sa.Column('consumed_at', sa.BigInteger(), nullable=True))
    op.create_index('ix_email_login_challenges_email', 'email_login_challenges', ['email'])
    op.create_index('ix_email_login_challenges_expires_at', 'email_login_challenges', ['expires_at'])


def downgrade():
    op.drop_table('email_login_challenges')
    with op.batch_alter_table('users') as batch:
        batch.drop_column('auth_version')
        batch.drop_column('email_version')
        batch.drop_column('email_verified_at')
