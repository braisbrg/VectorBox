"""add clerk_user_id and is_anonymous

Revision ID: d2e3f4g5h6i7
Revises: c1d2e3f4g5h6
Create Date: 2026-04-19 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'd2e3f4g5h6i7'
down_revision = 'c1d2e3f4g5h6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column('clerk_user_id', sa.String(255), nullable=True))
    op.add_column('users', sa.Column('is_anonymous', sa.Boolean(), nullable=False, server_default='false'))
    op.create_index('ix_users_clerk_user_id', 'users', ['clerk_user_id'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_users_clerk_user_id', 'users')
    op.drop_column('users', 'is_anonymous')
    op.drop_column('users', 'clerk_user_id')
