"""¿Siguen el payload, los vectores y las URIs de Letterboxd lo que dice su fuente? — 2026-11-03.

Nació de la auditoría del 2026-11-03 (CUADERNO E115–E118). Se encontró:
  - 1.451 vectores de la receta cruda bajo la marca de enriquecidos;
  - el payload desfasado de Postgres en siete filtros;
  - 1.584 puntos con la clave vieja `rating`;
  - valoraciones apuntando a otra película.
Nada de eso falló ni avisó: las guardas protegían lo que se escribía después y nadie miraba lo ya
escrito.

Tres comprobaciones, sin red:
  uris     cada `letterboxd_uri` contra su fila (`reconcile_letterboxd_movies.audit`).
  payload  cada punto contra `qdrant_payload(fila)`. Una clave ausente cuyo valor en la fila es
           vacío no cuenta: ausente y null filtran igual (playbook B1).
  vectors  el vector denso guardado contra el texto que producción embebería hoy
           (`embedding_service.embedding_text`), para todas las películas de biblioteca y una
           muestra del resto. Carga el modelo: unos 8 minutos de CPU con 500 de muestra.

Sale con 1 si queda algo. `--repair` regenera lo derivado desde su fuente: el payload con
`qdrant_payload` y el vector denso con `update_vectors`, sin tocar el sparse. Las URIs no se
arreglan aquí: `reconcile_letterboxd_movies.py --fix` actúa sólo en el caso seguro. Tras reparar
vectores tocan re-cluster, mood y tabla de vecinos (orden de docs/SCRIPTS_GUIDE.md).

    docker compose run --rm --no-deps backend python scripts/check_integrity.py
    docker compose run --rm --no-deps backend python scripts/check_integrity.py --repair --sample 1000
    docker compose run --rm --no-deps backend python scripts/check_integrity.py --no-vectors
"""
import argparse
import asyncio
import collections
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qdrant_client import models as qm
from sqlalchemy import select

from config import AsyncSessionLocal
from models.database import Movie, UserRating
from models.external_schemas import qdrant_payload
from scripts.reconcile_letterboxd_movies import audit as uri_audit
from services.qdrant_service import QdrantService

DRIFT_THRESHOLD = 0.99  # E115: lo sano está a ≥ 0,999 y lo roto por debajo de 0,95; no hay medio
_EMPTY = (None, [], "")


def _same(a, b):
    if isinstance(a, float) or isinstance(b, float):
        if a is None or b is None:
            return a is None and b is None
        return math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-6)
    if isinstance(a, list) and isinstance(b, list):
        return sorted(map(str, a)) == sorted(map(str, b))
    return a == b


def payload_problems(movies, payloads):
    """[(tmdb_id, kind, key)] con kind en missing / differs / extra.

    `movies`: filas con todo lo que lee `qdrant_payload`. `payloads`: tmdb_id → payload del punto.
    Una película sin punto no es asunto de este chequeo (lo es de la fase 7).
    """
    out = []
    for m in movies:
        got = payloads.get(m.tmdb_id)
        if got is None:
            continue
        want = qdrant_payload(m)
        for key, value in want.items():
            if key not in got:
                if value not in _EMPTY:
                    out.append((m.tmdb_id, "missing", key))
            elif not _same(value, got[key]):
                out.append((m.tmdb_id, "differs", key))
        out.extend((m.tmdb_id, "extra", key) for key in got if key not in want)
    return out


def vector_drift(stored, encoded, threshold=DRIFT_THRESHOLD):
    """[(tmdb_id, coseno)] de los vectores guardados que no son su texto. Dicts tmdb_id → vector."""
    out = []
    for tmdb_id, v in stored.items():
        e = encoded.get(tmdb_id)
        if e is None:
            continue
        cos = float(np.dot(v, e) / (np.linalg.norm(v) * np.linalg.norm(e)))
        if cos < threshold:
            out.append((tmdb_id, cos))
    return out


async def run(sample=500, vectors=True, repair=False):
    """Las tres comprobaciones. Devuelve un resumen; `ok` es False si queda algo sin arreglar."""
    async with AsyncSessionLocal() as db:
        movies = (await db.execute(select(Movie))).scalars().all()
        library = {t for (t,) in (await db.execute(
            select(Movie.tmdb_id).join(UserRating, UserRating.movie_id == Movie.id))).all()}
    qd = QdrantService()
    payloads, dense, offset = {}, {}, None
    while True:
        batch, offset = await qd.client.scroll(collection_name=qd.COLLECTION_NAME, limit=1024,
                                               offset=offset, with_payload=True, with_vectors=vectors)
        for p in batch:
            payloads[int(p.id)] = p.payload or {}
            if vectors:
                d = QdrantService._dense_of(p.vector)
                if d is not None:
                    dense[int(p.id)] = np.asarray(d, dtype=np.float32)
        if offset is None:
            break

    sure, shared, slugs = uri_audit(movies)
    problems = payload_problems(movies, payloads)
    summary = {
        "uris": {"sure": len(sure), "shared": len(shared), "slug": len(slugs)},
        "payload": dict(collections.Counter(kind for _, kind, _ in problems)),
        "payload_points": len({t for t, _, _ in problems}),
    }

    drifted = []
    if vectors:
        from services.embedding_service import embedding_text, get_model
        by_tmdb = {m.tmdb_id: m for m in movies}
        others = sorted(t for t in dense if t not in library)
        rng = np.random.default_rng()
        picked = sorted(library & set(dense)) + [
            int(t) for t in rng.choice(others, size=min(sample, len(others)), replace=False)]
        texts = [embedding_text({"overview": by_tmdb[t].overview, "genres": by_tmdb[t].genres,
                                 "keywords": by_tmdb[t].keywords,
                                 "text_override": by_tmdb[t].cinematic_description or None})
                 or by_tmdb[t].title or "Unknown" for t in picked]
        enc = get_model().encode(texts, batch_size=32, normalize_embeddings=True, convert_to_numpy=True)
        encoded = dict(zip(picked, enc))
        drifted = vector_drift({t: dense[t] for t in picked}, encoded)
        summary["vectors"] = {"checked": len(picked), "library": len(library & set(dense)),
                              "drifted": len(drifted),
                              "examples": [(by_tmdb[t].title, round(c, 3)) for t, c in drifted[:5]]}

    if repair:
        bad = {t for t, _, _ in problems}
        by_tmdb = {m.tmdb_id: m for m in movies}
        ops = [qm.OverwritePayloadOperation(overwrite_payload=qm.SetPayload(
            payload=qdrant_payload(by_tmdb[t]), points=[t])) for t in sorted(bad)]
        for s in range(0, len(ops), 500):
            await qd.client.batch_update_points(collection_name=qd.COLLECTION_NAME,
                                                update_operations=ops[s:s + 500], wait=True)
        for s in range(0, len(drifted), 256):
            await qd.client.update_vectors(collection_name=qd.COLLECTION_NAME, wait=True, points=[
                qm.PointVectors(id=t, vector={"": encoded[t].tolist()}) for t, _ in drifted[s:s + 256]])
        summary["repaired"] = {"payload_points": len(bad), "vectors": len(drifted)}

    summary["ok"] = not sure and not shared and not slugs and (
        repair or (not problems and not drifted))
    return summary


def main():
    ap = argparse.ArgumentParser(description="¿Payload, vectores y URIs siguen a su fuente?")
    ap.add_argument("--sample", type=int, default=500, help="películas fuera de biblioteca a comprobar")
    ap.add_argument("--no-vectors", action="store_true", help="sin modelo: sólo URIs y payload")
    ap.add_argument("--repair", action="store_true", help="regenera payload y vectores desde su fuente")
    a = ap.parse_args()
    summary = asyncio.run(run(sample=a.sample, vectors=not a.no_vectors, repair=a.repair))
    for key, value in summary.items():
        print(f"{key}: {value}")
    sys.exit(0 if summary["ok"] else 1)


if __name__ == "__main__":
    main()
