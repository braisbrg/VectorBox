"""streaming_changes: altas y bajas de catálogo por servicio y país

Revision ID: x2y3z4a5b6c7
Revises: w1x2y3z4a5b6
Create Date: 2026-08-11

Tabla nueva y vacía: no toca nada existente, así que no puede degradar el feed ni las
recomendaciones. La llena una pasada diaria contra MovieOfTheNight (1000 req/mes), y se
lee en local para las filas «llega a tus servicios» y «se va pronto».
"""
from alembic import op
import sqlalchemy as sa

revision = "x2y3z4a5b6c7"
down_revision = "w1x2y3z4a5b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "streaming_changes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tmdb_id", sa.Integer(), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("service", sa.String(length=40), nullable=False),
        sa.Column("change_type", sa.String(length=16), nullable=False),
        sa.Column("option_type", sa.String(length=16), nullable=True),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("link", sa.Text(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_streaming_changes_tmdb_id", "streaming_changes", ["tmdb_id"])
    op.create_index(
        "idx_streaming_change_unico", "streaming_changes",
        ["tmdb_id", "country_code", "service", "change_type"], unique=True,
    )
    op.create_index(
        "idx_streaming_change_lectura", "streaming_changes",
        ["country_code", "change_type", "effective_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_streaming_change_lectura", table_name="streaming_changes")
    op.drop_index("idx_streaming_change_unico", table_name="streaming_changes")
    op.drop_index("ix_streaming_changes_tmdb_id", table_name="streaming_changes")
    op.drop_table("streaming_changes")
