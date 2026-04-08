"""add is_rejected column to user_ratings

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f7
Create Date: 2026-03-27 20:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b2c3d4e5f6a7'
down_revision = 'a1b2c3d4e5f7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('user_ratings', sa.Column('is_rejected', sa.Boolean(), server_default='false', nullable=True))


def downgrade() -> None:
    op.drop_column('user_ratings', 'is_rejected')
