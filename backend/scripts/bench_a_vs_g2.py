"""A (centroide global) vs G2 (multi-anchor) — re-decidido con los 4 ejes.

Por qué se rehace: la decisión de 2026-05 salió de `experiment_signal_a_heldout.py`, que
(medido 2026-08-06) comparaba una reimplementación cuyo `_strategy_g2_topk` devolvía **1 y 0
películas** — un veredicto entre una lista de 40 y una vacía. Y su métrica hold-out premia
la canonicidad, así que tampoco servía para ordenar recomendadores.

Aquí se comparan las dos estrategias con los **4 ejes** (BACKLOG 2026-08-11), que no
necesitan hold-out ni predecir nada:

  coherencia  máx. coseno contra la biblioteca del usuario  (¿se parece a lo que ama?)
  percentil   dónde cae esa coherencia en el catálogo        (¿cómo de arriba?)
  calidad     VBS medio y % por debajo de 55
  variedad    ILD + géneros + décadas

G2 = la función de PRODUCCIÓN (`get_user_centric_recommendations`), no una réplica.
A  = búsqueda única contra el centroide de la biblioteca, que es lo que G2 sustituyó.
AZAR ancla la escala: un orden aleatorio debe caer sobre el percentil 50.

Uso:
    docker compose exec backend python scripts/bench_a_vs_g2.py
"""
from __future__ import annotations

import asyncio
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from sqlalchemy import select

from config import AsyncSessionLocal
from models.database import Movie, UserRating
from services.clustering_service import ClusteringService
from services.qdrant_service import QdrantService

from scripts.bench_synthetic_profiles import PREFIX, build_profiles, cleanup

K = 20
SEED = 77


def _norm(v):
    v = np.asarray(v, dtype=np.float32)
    n = float(np.linalg.norm(v))
    return v / n if n else v


def _ejes(ids, vecs, meta, L, dist):
    us = [t for t in ids if t in vecs]
    if not us:
        return None
    V = np.stack([_norm(vecs[t]) for t in us])
    coh = float(np.mean(np.max(V @ L.T, axis=1)))
    pct = 100.0 * float(np.mean(dist < coh))
    vbs = [meta[t]["vbs"] for t in ids if t in meta and meta[t]["vbs"] is not None]
    sim = V @ V.T
    n = len(us)
    ild = float((n * n - np.sum(sim)) / (n * (n - 1))) if n > 1 else 0.0
    dec = {(meta[t]["year"] // 10) * 10 for t in ids if t in meta and meta[t]["year"]}
    gen = {g for t in ids if t in meta for g in (meta[t]["genres"] or [])}
    return dict(n=len(ids), coh=coh, pct=pct,
                vbs=float(np.mean(vbs)) if vbs else 0.0,
                bajo=100.0 * len([v for v in vbs if v < 55]) / max(1, len(vbs)),
                ild=ild, gen=len(gen), dec=len(dec))


async def main() -> None:
    qd = QdrantService()
    rnd = random.Random(SEED)
    await cleanup()
    print("Perfiles sintéticos (metadatos, cero vectores):")
    perfiles = await build_profiles()
    objetivos = [("REAL 212", 212), ("REAL 210", 210)] + list(perfiles.items())

    try:
        async with AsyncSessionLocal() as db:
            cat = (await db.execute(
                select(Movie.tmdb_id, Movie.vectorbox_score, Movie.genres, Movie.year)
                .where(Movie.has_enriched_embedding.is_(True))
                .where(Movie.tmdb_id.isnot(None))
            )).all()
            meta = {t: {"vbs": v, "genres": g, "year": y} for t, v, g, y in cat}
            todos = [t for t, *_ in cat]

            for nombre, uid in objetivos:
                lib = (await db.execute(
                    select(Movie.tmdb_id)
                    .join(UserRating, UserRating.movie_id == Movie.id)
                    .where(UserRating.user_id == uid)
                    .where(Movie.has_enriched_embedding.is_(True))
                )).scalars().all()
                if len(lib) < 20:
                    continue
                vistas = set(lib)
                lm = await qd.get_vectors_batch(list(lib))
                L = np.stack([_norm(v) for v in lm.values()])

                libres = [t for t in todos if t not in vistas]
                cm = await qd.get_vectors_batch(libres)
                if not cm:
                    print(f"  {nombre}: get_vectors_batch devolvió 0 de {len(libres)} — saltado")
                    continue
                cid = list(cm)
                C = np.stack([_norm(cm[t]) for t in cid])
                dist = np.full(len(cid), -1.0, dtype=np.float32)
                for i in range(0, len(L), 512):
                    dist = np.maximum(dist, (C @ L[i:i + 512].T).max(axis=1))

                # G2 = producción
                g2 = [r.get("movie_id") for r in
                      await ClusteringService(qdrant=qd).get_user_centric_recommendations(uid, db, limit=K)][:K]

                # A = centroide global de la biblioteca, una sola búsqueda
                centro = _norm(L.mean(axis=0))
                hits = await qd.search_similar(list(centro), limit=K + len(vistas))
                a = [h["movie_id"] for h in hits if h["movie_id"] not in vistas][:K]

                azar = rnd.sample(libres, K)

                print(f"\n===== {nombre} (biblioteca {len(lib)}) =====")
                print(f"  {'estrategia':12s} | {'n':>3} | {'coher.':>7} | {'pctil':>6} | "
                      f"{'VBS':>5} | {'%<55':>5} | {'ILD':>6} | {'gén':>4} | {'déc':>4}")
                for etiqueta, ids in (("G2 (prod)", g2), ("A (centroide)", a), ("AZAR", azar)):
                    e = _ejes(ids, cm, meta, L, dist)
                    if not e:
                        print(f"  {etiqueta:12s} | (vacía)")
                        continue
                    print(f"  {etiqueta:12s} | {e['n']:3d} | {e['coh']:7.3f} | {e['pct']:5.1f}% | "
                          f"{e['vbs']:5.1f} | {e['bajo']:4.0f}% | {e['ild']:6.3f} | "
                          f"{e['gen']:4d} | {e['dec']:4d}")
    finally:
        n = await cleanup()
        print(f"\nlimpieza: {n} perfiles borrados")


if __name__ == "__main__":
    asyncio.run(main())
