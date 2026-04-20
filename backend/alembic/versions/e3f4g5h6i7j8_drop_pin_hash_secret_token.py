"""drop pin_hash and secret_token (legacy PIN auth removed in favor of Clerk)

Revision ID: e3f4g5h6i7j8
Revises: d2e3f4g5h6i7
Create Date: 2026-04-20 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "e3f4g5h6i7j8"
down_revision = "d2e3f4g5h6i7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_users_secret_token", table_name="users")
    op.drop_column("users", "secret_token")
    op.drop_column("users", "pin_hash")


def downgrade() -> None:
    op.add_column("users", sa.Column("pin_hash", sa.String(length=255), nullable=True))
    op.add_column(
        "users",
        sa.Column("secret_token", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_users_secret_token", "users", ["secret_token"], unique=True)
