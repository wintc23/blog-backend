"""stat event 2

Revision ID: f8b6cc74b896
Revises: 40049ae0492d
Create Date: 2022-04-11 01:59:30.194170

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = 'f8b6cc74b896'
down_revision = '40049ae0492d'
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
    op.add_column('stat_event', sa.Column('params', sa.Text(), nullable=True))
    op.alter_column('stat_event', 'name',
               existing_type=mysql.VARCHAR(collation='utf8mb4_unicode_ci', length=64),
               type_=sa.String(length=32),
               existing_nullable=True)
    op.create_index(op.f('ix_stat_event_name'), 'stat_event', ['name'], unique=False)
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index(op.f('ix_stat_event_name'), table_name='stat_event')
    op.alter_column('stat_event', 'name',
               existing_type=sa.String(length=32),
               type_=mysql.VARCHAR(collation='utf8mb4_unicode_ci', length=64),
               existing_nullable=True)
    op.drop_column('stat_event', 'params')
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