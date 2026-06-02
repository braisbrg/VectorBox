"""add is_excluded column to movies

Adds a soft-delete flag used by `MOVIE_QUALITY_GATE` to keep non-films
(UFC events, wrestling shows, recopilation vaults, etc.) out of every
recommendation surface — while leaving them visible in watchlist /
ratings / direct lookup paths that don't consult the gate.

Revision ID: q5r6s7t8u9v0
Revises: p4q5r6s7t8u9
Create Date: 2026-05-26 21:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'q5r6s7t8u9v0'
down_revision = 'p4q5r6s7t8u9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'movies',
        sa.Column('is_excluded', sa.Boolean(), server_default='false', nullable=False),
    )


def downgrade() -> None:
    op.drop_column('movies', 'is_excluded')
