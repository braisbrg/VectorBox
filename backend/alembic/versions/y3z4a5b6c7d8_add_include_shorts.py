"""include_shorts: toggle por usuario para cortometrajes en el feed

Revision ID: y3z4a5b6c7d8
Revises: x2y3z4a5b6c7
Create Date: 2026-08-17

Columna nueva con server_default false: el feed deja de servir cortos a todo el
mundo salvo que lo activen en ajustes. 1203 películas del catálogo duran <= 40
min y 800 de ellas pasaban el gate de calidad, así que la fila «para ti» podía
ofrecer un corto de 6 minutos junto a un largo. El umbral de 40 min es la
definición de la Academia, y está en recommendation_engine.SHORT_FILM_MAX_RUNTIME.
"""
from alembic import op
import sqlalchemy as sa

revision = "y3z4a5b6c7d8"
down_revision = "x2y3z4a5b6c7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("include_shorts", sa.Boolean(), server_default="false", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("users", "include_shorts")
