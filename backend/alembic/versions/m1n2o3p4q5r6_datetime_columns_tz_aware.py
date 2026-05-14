"""make_datetime_columns_tz_aware

Promotes 8 DateTime columns to TIMESTAMP WITH TIME ZONE to match the SQLAlchemy
models which were switched to ``DateTime(timezone=True)`` + ``datetime.now(timezone.utc)``
in commit c2c7929. Without this, the ORM declares TIMESTAMPTZ but the underlying
columns remain naive, producing silent wrong results when comparing
``datetime.now(timezone.utc)`` against stored values across DST or non-UTC clients.

Existing values are interpreted as UTC (the codebase has always written naive UTC
via ``datetime.utcnow()`` before the bump).

Revision ID: m1n2o3p4q5r6
Revises: l0m1n2o3p4q5
Create Date: 2026-05-14

"""
from typing import Sequence, Union
from alembic import op


revision: str = 'm1n2o3p4q5r6'
down_revision: Union[str, None] = 'l0m1n2o3p4q5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


COLUMNS = [
    ('users', 'created_at'),
    ('users', 'last_active_at'),
    ('movies', 'created_at'),
    ('movies', 'updated_at'),
    ('user_ratings', 'created_at'),
    ('user_clusters', 'created_at'),
    ('streaming_providers', 'created_at'),
    ('movie_availability', 'last_updated'),
]


def upgrade() -> None:
    for table, column in COLUMNS:
        op.execute(
            f"ALTER TABLE {table} "
            f"ALTER COLUMN {column} TYPE TIMESTAMP WITH TIME ZONE "
            f"USING {column} AT TIME ZONE 'UTC'"
        )


def downgrade() -> None:
    for table, column in COLUMNS:
        op.execute(
            f"ALTER TABLE {table} "
            f"ALTER COLUMN {column} TYPE TIMESTAMP WITHOUT TIME ZONE "
            f"USING {column} AT TIME ZONE 'UTC'"
        )
