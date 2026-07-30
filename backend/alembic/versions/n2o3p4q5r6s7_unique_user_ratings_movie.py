"""unique_user_ratings_movie

Adds a UNIQUE constraint on (user_id, movie_id) to prevent duplicate rating
rows. The current schema only had PRIMARY KEY (id) which allowed multiple
rows for the same (user, movie) pair — duplicates were reported for user 212
(film "The Teacher"). Pre-check at migration time showed 0 duplicates so the
constraint can be added safely; a defensive de-dup CTE is included for safety
if duplicates appear in other environments.

Revision ID: n2o3p4q5r6s7
Revises: m1n2o3p4q5r6
Create Date: 2026-05-15

"""
from typing import Sequence, Union
from alembic import op


revision: str = 'n2o3p4q5r6s7'
down_revision: Union[str, None] = 'm1n2o3p4q5r6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Defensive de-dup: keep the row with the highest id for each (user_id, movie_id).
    # No-op on environments that already have no duplicates.
    op.execute(
        """
        DELETE FROM user_ratings ur
        USING user_ratings ur2
        WHERE ur.user_id = ur2.user_id
          AND ur.movie_id = ur2.movie_id
          AND ur.id < ur2.id
        """
    )

    op.execute(
        """
        ALTER TABLE user_ratings
        ADD CONSTRAINT uq_user_ratings_user_movie UNIQUE (user_id, movie_id)
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE user_ratings DROP CONSTRAINT IF EXISTS uq_user_ratings_user_movie")
