"""guardar la POSICION de cada película en la watchlist de Letterboxd

Revision ID: v0w1x2y3z4a5
Revises: u9v0w1x2y3z4
Create Date: 2026-08-10

`sort_by=date_added` ordenaba por `movies.id DESC` — el orden en que la PELICULA
entró en nuestro catálogo, que no dice nada de cuándo la añadió el usuario.
Medido en el 212: `White Men Can't Jump` es el 4º de la página 1 en Letterboxd y
salía en el puesto 56 de 589.

`user_ratings.created_at` tampoco sirve, y esto es lo no obvio: es la fecha en que
NOSOTROS escribimos la fila. El import inicial del 212 metió 594 filas en 35
instantes, hasta 50 compartiendo el mismo segundo — dentro de esos bloques el
orden es lo que Postgres tenga a mano. Y en los lotes incrementales sale
INVERTIDO: insertamos en orden de Letterboxd (lo más nuevo primero), así que lo
primero insertado se lleva el `created_at` más pequeño, y ordenar DESC da la
vuelta al lote.

Letterboxd no publica fechas en la watchlist, sólo el orden de la página. Así que
lo que se guarda es la posición: 1 = lo primero de la página 1. La estampa el
scrape, que ya recorre las páginas en ese orden y la estaba tirando.

NULL = fila que ningún scrape ha visto todavía (import por ZIP). Se ordena
`NULLS LAST` con `movies.id DESC` de desempate: el comportamiento viejo para lo
que aún no tiene dato, sin inventarle una posición.

Sin índice: se lee ordenando la watchlist de UN usuario, cientos de filas, ya
filtradas por el índice de `(user_id, movie_id)`.
"""
import sqlalchemy as sa
from alembic import op

revision = "v0w1x2y3z4a5"
down_revision = "u9v0w1x2y3z4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_ratings", sa.Column("watchlist_rank", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("user_ratings", "watchlist_rank")
