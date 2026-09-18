"""Digest comments and managed image references."""
from alembic import op
import sqlalchemy as sa

revision = '20260917_rich_comments'
down_revision = '20260917_email_login'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('comments') as batch:
        batch.add_column(sa.Column('digest_id', sa.Integer(), nullable=True))
        batch.create_foreign_key('fk_comment_digest', 'ai_digests', ['digest_id'], ['id'])
        batch.create_index('ix_comments_digest_id', ['digest_id'])
    op.create_table('media_assets',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('storage_key', sa.String(255), nullable=False, unique=True),
        sa.Column('url', sa.String(1024), nullable=False),
        sa.Column('owner_id', sa.Integer(), nullable=True),
        sa.Column('mime_type', sa.String(64), nullable=False),
        sa.Column('byte_size', sa.Integer(), nullable=False),
        sa.Column('width', sa.Integer(), nullable=False),
        sa.Column('height', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('delete_after', sa.DateTime(), nullable=True),
        sa.Column('delete_attempts', sa.Integer(), nullable=False))
    op.create_index('ix_media_assets_delete_after', 'media_assets', ['delete_after'])
    op.create_table('media_references',
        sa.Column('asset_id', sa.String(32), sa.ForeignKey('media_assets.id'), primary_key=True),
        sa.Column('content_type', sa.String(40), primary_key=True),
        sa.Column('content_id', sa.String(64), primary_key=True))
    op.create_index('ix_media_reference_content', 'media_references', ['content_type', 'content_id'])


def downgrade():
    op.drop_table('media_references')
    op.drop_table('media_assets')
    with op.batch_alter_table('comments') as batch:
        batch.drop_index('ix_comments_digest_id')
        batch.drop_constraint('fk_comment_digest', type_='foreignkey')
        batch.drop_column('digest_id')
