"""add the two mood axes to movies

Revision ID: u9v0w1x2y3z4
Revises: t8u9v0w1x2y3
Create Date: 2026-08-04

Dos percentiles 0-100 por película: gravedad y humanidad. Salen de proyectar el
vector que YA está en Qdrant sobre dos direcciones fijas — no se re-embebe ni se
re-enriquece nada, así que esto no puede degradar el feed, los similares ni la
búsqueda (ver services/mood_axes.py para la medición completa).

Dos y no tres: la tercera componente del PCA valía el 9% de la varianza y los dos
intentos de definirla como eje salieron solapados con los otros (r=+0.67 y
r=-0.72). Estos dos están a r=+0.01.

NULL = todavía sin calcular. No 50, que afirmaría "esta película es
exactamente media" cuando lo que pasa es que nadie la ha mirado. Toda superficie
que lea estas columnas tiene que tratar el NULL como "sin dato" y no filtrarla
fuera silenciosamente.

Sin índice: el primer consumidor filtra dentro de Qdrant (el payload lleva las
mismas tres claves), y en Postgres esto se lee por película o para agregados de
20k filas. Si algún día se ordena el catálogo por un eje, se mide y se añade.
"""
import sqlalchemy as sa
from alembic import op

revision = "u9v0w1x2y3z4"
down_revision = "t8u9v0w1x2y3"
branch_labels = None
depends_on = None

COLUMNS = ("mood_gravedad", "mood_humanidad")


def upgrade() -> None:
    for name in COLUMNS:
        op.add_column("movies", sa.Column(name, sa.Float(), nullable=True))


def downgrade() -> None:
    for name in COLUMNS:
        op.drop_column("movies", name)
