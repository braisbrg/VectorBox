"""add pg_trgm for the typo fallback in the title searchbar

Revision ID: w1x2y3z4a5b6
Revises: v0w1x2y3z4a5
Create Date: 2026-08-10

TMDB no tiene búsqueda difusa, y eso deja la searchbar en CERO ante una errata de
una letra. Medido 2026-08-10 contra `/search/movie`:

    "intersteller"          0 resultados
    "shawshenk redemption"  0 resultados
    "shawshank"             4 resultados

Con trigramas sobre nuestro propio catálogo la respuesta correcta sale primera en
las 12 consultas probadas, y el umbral está medido, no elegido:

    intersteller           Interstellar             0.625   (2º: 0.350)
    shawshenk redemption   The Shawshank Redemption 0.667   (2º: 0.524)
    the godfater           The Godfather            0.733
    parasyte               Parasite                 0.500   <- el más flojo que acierta
    zzzqqq no existe nada  Evil Does Not Exist      0.370   <- el mejor que NO debe pasar

0.45 separa las dos poblaciones. Ver FUZZY_MIN_SIMILARITY en routers/search.py.

Sin índice GIN, a propósito: el fallback sólo se ejecuta cuando TMDB ya devolvió
cero, así que la ruta normal no lo paga nunca. Sin índice cuesta 159 ms de media y
215 ms el peor caso sobre 20k filas — frente al cero absoluto que se sirve hoy.
Tres índices GIN mantenidos para una ruta rara son peor negocio. Si algún día el
fallback se vuelve común, se mide y se añade.
"""
from alembic import op

revision = "w1x2y3z4a5b6"
down_revision = "v0w1x2y3z4a5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")


def downgrade() -> None:
    # Igual que unaccent: no se tira una extensión de la que puede depender otra
    # migración o una consulta escrita a mano.
    pass
