"""add imdb_vote_count to movies

Revision ID: i7j8k9l0m1n2
Revises: h6i7j8k9l0m1
Create Date: 2026-05-05 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'i7j8k9l0m1n2'
down_revision: Union[str, None] = 'h6i7j8k9l0m1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'movies',
        sa.Column('imdb_vote_count', sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('movies', 'imdb_vote_count')
