"""add_partial_indexes_user_ratings

Adds three partial/composite indexes to user_ratings for the most common
feed-generation query patterns, replacing sequential scans:

  - idx_user_ratings_watched   (user_id) WHERE is_watched = true
  - idx_user_ratings_watchlist (user_id) WHERE is_watchlist = true
  - idx_user_ratings_anchors   (user_id, rating, watched_date) WHERE rating >= 3.5
    → Signal A anchor candidate query

Also adds a composite index on movies(original_language) used by the
Subtitles Required niche theme and Signal C exoticism boost.

Revision ID: l0m1n2o3p4q5
Revises: k9l0m1n2o3p4
Create Date: 2026-05-14

"""
from typing import Sequence, Union
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'l0m1n2o3p4q5'
down_revision: Union[str, None] = 'k9l0m1n2o3p4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Partial index: WHERE is_watched = true — used by every feed signal
    # to exclude already-watched movies and fetch anchor candidates.
    # Note: CONCURRENTLY is not used here because Alembic runs inside a transaction.
    # These are still idempotent via IF NOT EXISTS.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_user_ratings_watched
        ON user_ratings (user_id)
        WHERE is_watched = true
        """
    )

    # Partial index: WHERE is_watchlist = true — watchlist feed + group recs
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_user_ratings_watchlist
        ON user_ratings (user_id)
        WHERE is_watchlist = true
        """
    )

    # Composite partial index for Signal A anchor query:
    # SELECT ... FROM user_ratings WHERE user_id = $1 AND (rating >= 3.5 OR is_liked)
    # ORDER BY rating DESC, watched_date DESC LIMIT 500
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_user_ratings_anchors
        ON user_ratings (user_id, rating DESC NULLS LAST, watched_date DESC NULLS LAST)
        WHERE rating >= 3.5 OR is_liked = true
        """
    )

    # Partial index for rejected/low-rated items — used by anti-vector computation
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_user_ratings_negative
        ON user_ratings (user_id)
        WHERE is_rejected = true OR rating <= 3.0
        """
    )

    # Composite index on movies for language filtering (Subtitles Required theme)
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_movies_language_score
        ON movies (original_language, vectorbox_score DESC NULLS LAST)
        WHERE vectorbox_score IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_user_ratings_watched")
    op.execute("DROP INDEX IF EXISTS idx_user_ratings_watchlist")
    op.execute("DROP INDEX IF EXISTS idx_user_ratings_anchors")
    op.execute("DROP INDEX IF EXISTS idx_user_ratings_negative")
    op.execute("DROP INDEX IF EXISTS idx_movies_language_score")
