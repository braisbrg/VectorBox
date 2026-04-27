"""add embedding_quality_score column

Revision ID: g5h6i7j8k9l0
Revises: f4g5h6i7j8k9
Create Date: 2026-04-27 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "g5h6i7j8k9l0"
down_revision = "f4g5h6i7j8k9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "movies",
        sa.Column("embedding_quality_score", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("movies", "embedding_quality_score")
