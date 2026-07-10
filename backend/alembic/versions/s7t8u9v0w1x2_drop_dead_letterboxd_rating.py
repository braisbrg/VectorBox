"""drop the dead movies.letterboxd_rating column

F5 cleanup. Nothing ever wrote this column (grep-verified: every
`letterboxd_rating` write in the codebase targets scraper/trending cache dicts
or the FeedItem response field, never `Movie.letterboxd_rating`). The
popular_letterboxd section reads the scraped rating from the trending cache
(recommendation_engine.py `rating_by_tmdb`), and the movie-detail LBXD stat was
always NULL — its reads are removed in the same commit. The FeedItem response
field of the same name is unrelated and stays.

Revision ID: s7t8u9v0w1x2
Revises: r6s7t8u9v0w1
Create Date: 2026-07-10 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 's7t8u9v0w1x2'
down_revision = 'r6s7t8u9v0w1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column('movies', 'letterboxd_rating')


def downgrade() -> None:
    op.add_column('movies', sa.Column('letterboxd_rating', sa.Float(), nullable=True))
