"""body_html to mediumtext

BlockNote's full-HTML export wraps every block in
`.bn-block-outer > .bn-block > .bn-block-content` markup, so a
migrated long article can balloon past plain TEXT's 65 KB cap
("Data too long for column 'body_html'"). Lift to MEDIUMTEXT (16 MB).

Revision ID: 8b6141921bce
Revises: 881f078af041
Create Date: 2026-04-17 23:10:32.124537

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = '8b6141921bce'
down_revision = '881f078af041'
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        'posts', 'body_html',
        existing_type=sa.Text(),
        type_=mysql.MEDIUMTEXT(),
        existing_nullable=True,
    )


def downgrade():
    op.alter_column(
        'posts', 'body_html',
        existing_type=mysql.MEDIUMTEXT(),
        type_=sa.Text(),
        existing_nullable=True,
    )
