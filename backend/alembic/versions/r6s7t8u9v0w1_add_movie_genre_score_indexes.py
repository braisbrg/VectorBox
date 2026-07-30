"""add genre (GIN) + vectorbox_score indexes to movies

Audit PERF-C. The recommendation genre-fallback (recommendation_engine.py:279)
runs `WHERE vectorbox_score > N AND genres && ARRAY[...] ORDER BY vectorbox_score
DESC` — a full seq-scan over ~13.5k rows (measured 57ms). `genres` is queried
exclusively via `Movie.genres.overlap(...)` (the PostgreSQL `&&` operator), which
a GIN index accelerates; selective `vectorbox_score` range filters (> 70 / > 90)
benefit from a btree. Measured after both indexes: the same query drops to ~3.5ms
(BitmapAnd of the two indexes). The btree is correctly ignored for low-selectivity
filters (e.g. `>= 55` matches ~53% of rows), so it never hurts.

`idx_movie_year_genre` (btree over `(year, genres[])`) is intentionally left as-is
— it's useless for array containment but harmless, and dropping it is out of scope.

Revision ID: r6s7t8u9v0w1
Revises: q5r6s7t8u9v0
Create Date: 2026-06-22 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'r6s7t8u9v0w1'
down_revision = 'q5r6s7t8u9v0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # GIN index for `genres && ARRAY[...]` overlap queries.
    op.create_index(
        'ix_movies_genres_gin', 'movies', ['genres'],
        postgresql_using='gin',
    )
    # Btree for selective vectorbox_score range filters + the genre-fallback
    # BitmapAnd. DESC NULLS LAST matches the dominant `ORDER BY ... DESC` usage.
    op.create_index(
        'ix_movies_vectorbox_score', 'movies',
        [sa.text('vectorbox_score DESC NULLS LAST')],
    )


def downgrade() -> None:
    op.drop_index('ix_movies_vectorbox_score', table_name='movies')
    op.drop_index('ix_movies_genres_gin', table_name='movies')
