"""add upcoming release columns and last_metadata_refresh

Revision ID: f4g5h6i7j8k9
Revises: e3f4g5h6i7j8
Create Date: 2026-04-25 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "f4g5h6i7j8k9"
down_revision = "e3f4g5h6i7j8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("movies", sa.Column("release_date_us", sa.Date(), nullable=True))
    op.add_column("movies", sa.Column("release_date_es", sa.Date(), nullable=True))
    op.add_column("movies", sa.Column("release_date_ww", sa.Date(), nullable=True))
    op.add_column(
        "movies",
        sa.Column("is_upcoming", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column("movies", sa.Column("last_metadata_refresh", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("movies", "last_metadata_refresh")
    op.drop_column("movies", "is_upcoming")
    op.drop_column("movies", "release_date_ww")
    op.drop_column("movies", "release_date_es")
    op.drop_column("movies", "release_date_us")
