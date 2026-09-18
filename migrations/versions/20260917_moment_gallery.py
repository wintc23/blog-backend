"""Add optional occurrence time and ordered, captioned moment images."""
import json
from alembic import op
import sqlalchemy as sa

revision = '20260917_moment_gallery'
down_revision = '20260917_rich_comments'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('life_moments') as batch:
        batch.add_column(sa.Column('occurred_at', sa.DateTime(), nullable=True))
        batch.add_column(sa.Column('images_json', sa.Text(), nullable=True))
        batch.alter_column('text', existing_type=sa.String(280), type_=sa.Text(), existing_nullable=False)
        batch.drop_index('ix_life_moments_chronology')
        batch.create_index('ix_life_moments_chronology', ['date', 'occurred_at', 'created_at', 'id'])
    connection = op.get_bind()
    rows = connection.execute(sa.text('SELECT id, image_url, image_alt FROM life_moments')).fetchall()
    for row in rows:
        images = [{'url': row.image_url, 'description': row.image_alt or ''}] if row.image_url else []
        connection.execute(sa.text('UPDATE life_moments SET images_json = :images WHERE id = :id'),
                           images=json.dumps(images, ensure_ascii=False), id=row.id)


def downgrade():
    # Preserve longer text on rollback; older apps still enforce their own 280-character limit.
    with op.batch_alter_table('life_moments') as batch:
        batch.drop_index('ix_life_moments_chronology')
        batch.create_index('ix_life_moments_chronology', ['date', 'created_at', 'id'])
        batch.drop_column('images_json')
        batch.drop_column('occurred_at')
