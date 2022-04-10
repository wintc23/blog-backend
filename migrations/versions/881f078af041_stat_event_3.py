"""stat event 3

Revision ID: 881f078af041
Revises: f8b6cc74b896
Create Date: 2022-04-11 02:10:51.716251

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = '881f078af041'
down_revision = 'f8b6cc74b896'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.alter_column('comments', 'hide',
               existing_type=mysql.TINYINT(display_width=1),
               type_=sa.Boolean(),
               existing_nullable=True)
    op.alter_column('friend_link', 'hide',
               existing_type=mysql.TINYINT(display_width=1),
               type_=sa.Boolean(),
               existing_nullable=True)
    op.alter_column('friend_link', 'logo',
               existing_type=mysql.MEDIUMTEXT(collation='utf8mb4_unicode_ci'),
               type_=sa.Text(length=65535),
               existing_nullable=True)
    op.alter_column('messages', 'hide',
               existing_type=mysql.TINYINT(display_width=1),
               type_=sa.Boolean(),
               existing_nullable=True)
    op.alter_column('post_type', 'default',
               existing_type=mysql.TINYINT(display_width=1),
               type_=sa.Boolean(),
               existing_nullable=True)
    op.alter_column('posts', 'hide',
               existing_type=mysql.TINYINT(display_width=1),
               type_=sa.Boolean(),
               existing_nullable=True)
    op.alter_column('roles', 'default',
               existing_type=mysql.TINYINT(display_width=1),
               type_=sa.Boolean(),
               existing_nullable=True)
    op.create_index(op.f('ix_stat_event_ip'), 'stat_event', ['ip'], unique=False)
    op.create_index(op.f('ix_stat_event_visitor_id'), 'stat_event', ['visitor_id'], unique=False)
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index(op.f('ix_stat_event_visitor_id'), table_name='stat_event')
    op.drop_index(op.f('ix_stat_event_ip'), table_name='stat_event')
    op.alter_column('roles', 'default',
               existing_type=sa.Boolean(),
               type_=mysql.TINYINT(display_width=1),
               existing_nullable=True)
    op.alter_column('posts', 'hide',
               existing_type=sa.Boolean(),
               type_=mysql.TINYINT(display_width=1),
               existing_nullable=True)
    op.alter_column('post_type', 'default',
               existing_type=sa.Boolean(),
               type_=mysql.TINYINT(display_width=1),
               existing_nullable=True)
    op.alter_column('messages', 'hide',
               existing_type=sa.Boolean(),
               type_=mysql.TINYINT(display_width=1),
               existing_nullable=True)
    op.alter_column('friend_link', 'logo',
               existing_type=sa.Text(length=65535),
               type_=mysql.MEDIUMTEXT(collation='utf8mb4_unicode_ci'),
               existing_nullable=True)
    op.alter_column('friend_link', 'hide',
               existing_type=sa.Boolean(),
               type_=mysql.TINYINT(display_width=1),
               existing_nullable=True)
    op.alter_column('comments', 'hide',
               existing_type=sa.Boolean(),
               type_=mysql.TINYINT(display_width=1),
               existing_nullable=True)
    # ### end Alembic commands ###
