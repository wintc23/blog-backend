"""Comments on life moments share existing moderation and notifications."""
from alembic import op
import sqlalchemy as sa
revision = '20260919_moment_comments'
down_revision = '20260919_album_photo_visibility'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('comments') as batch:
        batch.add_column(sa.Column('moment_id', sa.String(36), nullable=True))
        batch.create_index('ix_comments_moment_id', ['moment_id'])
        batch.create_foreign_key('fk_comments_moment', 'life_moments', ['moment_id'], ['id'])


def downgrade():
    with op.batch_alter_table('comments') as batch:
        batch.drop_constraint('fk_comments_moment', type_='foreignkey')
        batch.drop_index('ix_comments_moment_id')
        batch.drop_column('moment_id')
