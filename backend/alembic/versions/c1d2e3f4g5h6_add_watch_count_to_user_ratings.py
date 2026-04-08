"""add watch_count to user_ratings

Revision ID: c1d2e3f4g5h6
Revises: b2c3d4e5f6a7
Create Date: 2026-04-03 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c1d2e3f4g5h6'
down_revision = 'b2c3d4e5f6a7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('user_ratings', sa.Column('watch_count', sa.Integer(), server_default='1', nullable=True))


def downgrade() -> None:
    op.drop_column('user_ratings', 'watch_count')
