"""Calcula los tres ejes de mood de todo el catálogo y los guarda.

Lee los vectores que YA están en Qdrant, los proyecta sobre las tres direcciones
de `services/mood_axes.py` y escribe el percentil 0-100 en Postgres y en el
payload de Qdrant. **No re-embebe ni re-enriquece nada**: ningún vector se
modifica, así que el feed, los similares y la búsqueda no pueden verse afectados.

El payload se estampa además de las columnas porque el primer consumidor filtra
DENTRO de la búsqueda vectorial — filtrar después de un fetch acotado es la
inanición por post-filtro que este proyecto ya se comió una vez.

Uso:
    docker compose exec backend python scripts/compute_mood_axes.py --dry-run
    docker compose exec backend python scripts/compute_mood_axes.py
"""
import argparse
import asyncio
import logging
import os
import sys

sys.path.append(os.getcwd())

import numpy as np
from qdrant_client import models as qmodels
from sqlalchemy import bindparam, select, update

from config import AsyncSessionLocal
from models.database import Movie
from services.mood_axes import (AXES, ANCHOR_GROUPS, CENTERING_ALPHA,
                                axis_directions, center, to_percentile, unit)
from services.qdrant_service import QdrantService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("compute_mood_axes")

BATCH = 500


async def load_catalogue(q: QdrantService):
    """(ids, matriz) de todo lo que tenga vector. Scroll porque son 20k puntos."""
    ids, vecs, offset = [], [], None
    while True:
        points, offset = await q.client.scroll(
            collection_name=q.COLLECTION_NAME, limit=2000,
            offset=offset, with_vectors=True, with_payload=False)
        for p in points:
            dense = QdrantService._dense_of(p.vector)
            if dense is not None:
                ids.append(p.id)
                vecs.append(dense)
        if offset is None:
            break
    return ids, np.asarray(vecs, dtype=np.float32)


async def main(dry_run: bool):
    q = QdrantService()
    try:
        ids, X = await load_catalogue(q)
        logger.info("vectores leidos: %s", X.shape)
        Xc = center(X, CENTERING_ALPHA)
        idx = {tmdb_id: i for i, tmdb_id in enumerate(ids)}

        async with AsyncSessionLocal() as db:
            rows = (await db.execute(select(Movie.tmdb_id, Movie.title))).all()
        # Primer título gana: dos películas pueden compartir nombre y un ancla
        # tiene que resolver siempre a la misma.
        by_title = {}
        for tmdb_id, title in rows:
            by_title.setdefault((title or "").lower(), tmdb_id)

        group_vectors, missing = {}, {}
        for group, titles in ANCHOR_GROUPS.items():
            found = [Xc[idx[by_title[t.lower()]]] for t in titles
                     if by_title.get(t.lower()) in idx]
            absent = [t for t in titles if by_title.get(t.lower()) not in idx]
            if absent:
                missing[group] = absent
            # Con menos de 4 el grupo lo define un puñado de películas y deja de
            # ser una dirección; mejor caerse ruidosamente que emitir basura.
            if len(found) >= 4:
                group_vectors[group] = np.mean(found, axis=0)
            else:
                logger.error("grupo %r con solo %s anclas: se descarta", group, len(found))
        for group, absent in missing.items():
            logger.warning("anclas no encontradas en %r: %s", group, absent)

        directions = axis_directions(group_vectors)
        if len(directions) != len(AXES):
            logger.error("solo se pudieron construir %s de %s ejes: %s",
                         len(directions), len(AXES), list(directions))
            if not directions:
                return

        scores = {axis: to_percentile(Xc @ d) for axis, d in directions.items()}

        # Los ejes tienen que seguir siendo poco redundantes: si dos se pisan,
        # uno de los dos no está midiendo nada nuevo y hay que verlo aquí, no
        # tres semanas después en la UI.
        names = list(scores)
        logger.info("correlacion entre ejes (crudos):")
        raw = {a: Xc @ d for a, d in directions.items()}
        for a in names:
            logger.info("   %-12s %s", a, "  ".join(
                f"{b}={np.corrcoef(raw[a], raw[b])[0,1]:+.2f}" for b in names))

        if dry_run:
            logger.info("DRY RUN — no se escribe nada")
            for axis in names:
                s = scores[axis]
                top = np.argsort(s)[::-1][:5]
                logger.info("%s -> alto: %s", axis,
                            ", ".join(str(ids[i]) for i in top))
            return

        # --- Postgres, executemany por lotes (una UPDATE por película sería 20k
        # viajes de ida y vuelta)
        # Sobre la TABLA, no sobre la entidad ORM: con la entidad, SQLAlchemy 2.0
        # lee el executemany como "bulk update por clave primaria" y exige `id`
        # en cada fila, cuando aquí se localiza por tmdb_id.
        movies = Movie.__table__
        stmt = (update(movies)
                .where(movies.c.tmdb_id == bindparam("b_tmdb"))
                .values(**{f"mood_{a}": bindparam(f"b_{a}") for a in names}))
        async with AsyncSessionLocal() as db:
            for start in range(0, len(ids), BATCH):
                params = [
                    {"b_tmdb": ids[i], **{f"b_{a}": float(scores[a][i]) for a in names}}
                    for i in range(start, min(start + BATCH, len(ids)))
                ]
                await db.execute(stmt, params)
                await db.commit()
                logger.info("postgres: %s/%s", min(start + BATCH, len(ids)), len(ids))

        # --- payload de Qdrant, para poder filtrar en origen
        for start in range(0, len(ids), BATCH):
            ops = [
                qmodels.SetPayloadOperation(set_payload=qmodels.SetPayload(
                    payload={f"mood_{a}": float(scores[a][i]) for a in names},
                    points=[ids[i]],
                ))
                for i in range(start, min(start + BATCH, len(ids)))
            ]
            await q.client.batch_update_points(
                collection_name=q.COLLECTION_NAME, update_operations=ops)
            logger.info("qdrant: %s/%s", min(start + BATCH, len(ids)), len(ids))

        logger.info("listo: %s peliculas, ejes %s", len(ids), names)
    finally:
        await q.aclose()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="calcula y comprueba, no escribe")
    args = ap.parse_args()
    asyncio.run(main(args.dry_run))
