"""about_me

Revision ID: 904375a6a656
Revises: c3798bcb50f2
Create Date: 2019-04-18 21:22:36.480546

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '904375a6a656'
down_revision = 'c3798bcb50f2'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('post_type', sa.Column('sort', sa.Integer(), nullable=True))
    op.add_column('post_type', sa.Column('special', sa.SmallInteger(), nullable=True))
    op.create_index(op.f('ix_post_type_alias'), 'post_type', ['alias'], unique=False)
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index(op.f('ix_post_type_alias'), table_name='post_type')
    op.drop_column('post_type', 'special')
    op.drop_column('post_type', 'sort')
    # ### end Alembic commands ###
