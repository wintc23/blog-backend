"""Store life updates as independent, indefinitely appendable records."""
import json
from datetime import datetime
from alembic import op
import sqlalchemy as sa

revision = '20260916_life_moments'
down_revision = '20260916_profile_moments'
branch_labels = None
depends_on = None


def upgrade():
    table = op.create_table(
        'life_moments',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('category', sa.String(16), nullable=False),
        sa.Column('text', sa.String(280), nullable=False),
        sa.Column('image_url', sa.Text(), nullable=False),
        sa.Column('image_alt', sa.String(120), nullable=False),
        sa.Column('location', sa.String(60), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        mysql_engine='InnoDB', mysql_charset='utf8mb4',
    )
    op.create_index('ix_life_moments_chronology', 'life_moments', ['date', 'created_at', 'id'])
    # Keep the original JSON as a migration backup, but read new posts from this table.
    previous = op.get_bind().execute(sa.text('SELECT moments_json FROM personal_profiles WHERE id = 1')).scalar()
    now = datetime.utcnow()
    rows = []
    for moment in json.loads(previous or '[]'):
        rows.append(dict(
            id=moment['id'], date=datetime.strptime(moment['date'], '%Y-%m-%d').date(),
            category=moment['category'], text=moment['text'], image_url=moment['image_url'],
            image_alt=moment.get('image_alt', ''), location=moment.get('location', ''),
            created_at=now, updated_at=now,
        ))
    if rows:
        op.bulk_insert(table, rows)


def downgrade():
    op.drop_table('life_moments')
