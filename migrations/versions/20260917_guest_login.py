"""Shared rate limits for guest registration and participation."""
from alembic import op
import sqlalchemy as sa

revision = '20260917_guest_login'
down_revision = '20260917_portfolio_intro'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('guest_rate_limits',
        sa.Column('key', sa.String(64), primary_key=True),
        sa.Column('hits', sa.Integer, nullable=False),
        sa.Column('expires_at', sa.BigInteger, nullable=False))
    op.create_index('ix_guest_rate_limits_expires_at', 'guest_rate_limits', ['expires_at'])


def downgrade():
    op.drop_table('guest_rate_limits')
