"""add_extended_movie_metadata

Adds 10 metadata columns to `movies` for extra OMDb / TMDB signals that
refresh_metadata.py was already fetching but not persisting:

  OMDb:
    - mpaa_rating       OMDb `Rated` (G/PG/PG-13/R/NC-17/NR/TV-14)
    - awards_text       OMDb `Awards` raw string
    - oscar_wins        parsed Oscar wins count (0 default)
    - omdb_countries    OMDb `Country` split into array
    - omdb_languages    OMDb `Language` split into array

  TMDB:
    - collection_id     TMDB belongs_to_collection.id
    - collection_name   TMDB belongs_to_collection.name
    - is_adult          TMDB adult (porn filter)
    - tagline           TMDB tagline
    - backdrop_path     TMDB backdrop_path (hero image for Inspector)

Uses `ADD COLUMN IF NOT EXISTS` to be idempotent — pre-existing orphan
columns (backdrop_path and collection_id were already in the schema but
not tracked by alembic) are left untouched.

Revision ID: o3p4q5r6s7t8
Revises: n2o3p4q5r6s7
Create Date: 2026-05-15

"""
from typing import Sequence, Union
from alembic import op


revision: str = 'o3p4q5r6s7t8'
down_revision: Union[str, None] = 'n2o3p4q5r6s7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE movies ADD COLUMN IF NOT EXISTS mpaa_rating VARCHAR(10)")
    op.execute("ALTER TABLE movies ADD COLUMN IF NOT EXISTS awards_text TEXT")
    op.execute("ALTER TABLE movies ADD COLUMN IF NOT EXISTS oscar_wins INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE movies ADD COLUMN IF NOT EXISTS omdb_countries VARCHAR[]")
    op.execute("ALTER TABLE movies ADD COLUMN IF NOT EXISTS omdb_languages VARCHAR[]")
    op.execute("ALTER TABLE movies ADD COLUMN IF NOT EXISTS collection_id INTEGER")
    op.execute("ALTER TABLE movies ADD COLUMN IF NOT EXISTS collection_name VARCHAR(200)")
    op.execute("ALTER TABLE movies ADD COLUMN IF NOT EXISTS is_adult BOOLEAN NOT NULL DEFAULT false")
    op.execute("ALTER TABLE movies ADD COLUMN IF NOT EXISTS tagline TEXT")
    op.execute("ALTER TABLE movies ADD COLUMN IF NOT EXISTS backdrop_path VARCHAR(255)")

    # Partial index on oscar_wins for the planned "Oscar winners" feed section.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_movies_oscar_wins
        ON movies (oscar_wins DESC)
        WHERE oscar_wins > 0
        """
    )

    # Index on collection_id for "More from this franchise" lookup.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_movies_collection_id
        ON movies (collection_id)
        WHERE collection_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_movies_collection_id")
    op.execute("DROP INDEX IF EXISTS idx_movies_oscar_wins")
    for col in (
        "backdrop_path", "tagline", "is_adult", "collection_name", "collection_id",
        "omdb_languages", "omdb_countries", "oscar_wins", "awards_text", "mpaa_rating",
    ):
        op.execute(f"ALTER TABLE movies DROP COLUMN IF EXISTS {col}")
