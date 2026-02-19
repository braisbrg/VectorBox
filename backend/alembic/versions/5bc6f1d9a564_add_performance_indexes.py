"""add_performance_indexes

Revision ID: 5bc6f1d9a564
Revises: 5daf98544a53
Create Date: 2026-01-08 00:23:52.253131

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5bc6f1d9a564'
down_revision: Union[str, None] = '5daf98544a53'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index('idx_movie_vectorbox_score', 'movies', ['vectorbox_score'], unique=False)
    op.create_index('idx_movie_letterboxd_rating', 'movies', ['letterboxd_rating'], unique=False)
    op.create_index('idx_movie_popularity', 'movies', ['popularity'], unique=False)
    op.create_index('idx_movie_vote_count', 'movies', ['vote_count'], unique=False)


def downgrade() -> None:
    op.drop_index('idx_movie_vote_count', table_name='movies')
    op.drop_index('idx_movie_popularity', table_name='movies')
    op.drop_index('idx_movie_letterboxd_rating', table_name='movies')
    op.drop_index('idx_movie_vectorbox_score', table_name='movies')
