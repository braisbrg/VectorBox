"""¿Sobreviven suficientes películas a los filtros de la recomendación de grupo?

Un post-filtro sobre una búsqueda acotada se queda sin material EN SILENCIO: el
endpoint devuelve tres películas y parece que el grupo no tiene gustos comunes,
cuando lo que pasó es que el pool se agotó. Ya costó un barrido de diez sitios en
este repo (ver la nota de post-filter starvation en el BACKLOG), así que los filtros
nuevos de año y calidad no se dan por buenos sin medir cuántas pasan.

    docker compose exec backend python scripts/audit_group_filters.py

Mide sobre el pool REAL de un grupo real: cuántos candidatos de los 50 sobreviven a
cada combinación. La cifra que importa no es la media, es cuántas veces la fila baja
del mínimo servible.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date

from config import AsyncSessionLocal
from services.qdrant_service import QdrantService
from services.rss_service import RSSService
from services.tmdb_client import TMDBClient

GRUPO = ["braisbg", "haleks"]

# (etiqueta, year_min, year_max, min_score, max_runtime)
CASOS = [
    ("sin filtros",            None, None, None, None),
    ("solo 90 min",            None, None, None, 90),
    ("desde 2000",             2000, None, None, None),
    ("desde 2010",             2010, None, None, None),
    ("solo los 90",            1990, 1999, None, None),
    ("Q >= 70",                None, None, 70,   None),
    ("Q >= 80",                None, None, 80,   None),
    ("Q >= 85",                None, None, 85,   None),
    ("desde 2000 + Q>=70",     2000, None, 70,   None),
    ("desde 2010 + Q>=80",     2010, None, 80,   None),
    ("2010+ Q>=80 90min",      2010, None, 80,   90),
    ("los 90 + Q>=85",         1990, 1999, 85,   None),
]

MINIMO_SERVIBLE = 5   # por debajo de esto la fila no es una recomendación, es un resto


def pasa(movie, ymin, ymax, qmin, rt) -> bool:
    if movie.is_upcoming or not movie.year or movie.year > date.today().year or not movie.runtime:
        return False
    if rt and (movie.runtime is None or movie.runtime > rt):
        return False
    if ymin and movie.year < ymin:
        return False
    if ymax and movie.year > ymax:
        return False
    if qmin is not None and (movie.vectorbox_score is None or movie.vectorbox_score < qmin):
        return False
    return True


async def main() -> int:
    """Dos columnas, y la comparación entre ellas es el hallazgo.

    POST: cuántas del pool SIN filtrar sobrevivirían al filtro aplicado después.
          Es lo que hacía la primera versión, y es la que se agota.
    ORIGEN: cuántas devuelve el generador cuando el filtro entra ANTES de rankear
          (`session_filters`), que es como funciona hoy y como filtran las filas
          anchas del feed.
    """
    tmdb, qdrant = TMDBClient(), QdrantService()
    from sqlalchemy import select
    from models.database import Movie

    async with AsyncSessionLocal() as db:
        rss = RSSService(db, tmdb=tmdb, qdrant=qdrant)
        crudo = await rss.get_group_recommendations_hybrid(GRUPO, limit=200)
        if not crudo:
            print("  el grupo no devolvió candidatos — nada que medir")
            return 1
        ids = [r["tmdb_id"] for r in crudo]
        movies = {
            m.tmdb_id: m
            for m in (await db.execute(select(Movie).where(Movie.tmdb_id.in_(ids)))).scalars().all()
        }
        pool = [movies[i] for i in ids if i in movies]

        print(f"  grupo {GRUPO} · pool sin filtrar: {len(pool)}\n")
        print("  %-22s %-12s %-12s %s" % ("filtro", "POST", "ORIGEN", "veredicto"))
        problemas = 0
        for etiqueta, ymin, ymax, qmin, rt in CASOS:
            post = sum(1 for m in pool if pasa(m, ymin, ymax, qmin, rt))
            sf = None
            if any(v is not None for v in (ymin, ymax, qmin, rt)):
                sf = {"year_min": ymin, "year_max": ymax, "min_score": qmin, "max_runtime": rt}
            origen = len(await rss.get_group_recommendations_hybrid(GRUPO, limit=200, session_filters=sf))
            if origen == 0:
                veredicto, problemas = "VACÍA", problemas + 1
            elif origen < MINIMO_SERVIBLE:
                veredicto, problemas = f"< {MINIMO_SERVIBLE}", problemas + 1
            else:
                veredicto = "ok"
            print("  %-22s %-12s %-12s %s" % (
                etiqueta, f"{post}/{len(pool)}", f"{origen}/{len(pool)}", veredicto))

    print(f"\n  combinaciones por debajo de {MINIMO_SERVIBLE} filtrando en ORIGEN: "
          f"{problemas}/{len(CASOS)}")
    if problemas:
        print("  → esas piden de verdad algo que el grupo no tiene; comprueba que no")
        print("    sea el pool antes de tocar nada (sube `limit` y vuelve a medir).")
    await tmdb.aclose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
