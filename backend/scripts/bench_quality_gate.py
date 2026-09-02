"""¿Debe la Señal A tener un tope duro de calidad? Cuatro variantes, medidas.

Contexto: `MIN_QUALITY_SCORE = 55` se puso cuando el catálogo era peor y lo de debajo era
ruido. Medido 2026-08-06, hoy excluye gustos enteros: un perfil de horror 1975-99 tiene VBS
medio 50.3, así que sus películas favoritas están por debajo del umbral POR CONSTRUCCIÓN —
producción las encuentra y el gate las tira.

Variantes (todas sobre la MISMA salida cruda de producción, mismos splits, pareadas):
  cap55     tope duro en 55 — lo actual
  sin_cap   sin tope: sólo el orden RRF
  blando    sin tope, pero re-ordena por  rrf · (VBS/100)  → prioriza sin excluir
  usuario   tope adaptativo: percentil 10 del VBS de las películas QUE EL USUARIO YA VE.
            Si sólo ves cine de nota baja, tu suelo baja contigo.

Se reportan dos cosas a la vez, porque una sin la otra engaña:
  - aciertos sobre las ocultas (¿encuentra lo que te gusta?)
  - VBS medio de la lista devuelta (¿a costa de recomendarte basura?)

El denominador es «ocultas con embedding enriquecido», SIN condición de VBS: el coste del
tope tiene que verse como aciertos perdidos, que es justo lo que se está midiendo.

Uso:
    docker compose exec backend python scripts/bench_quality_gate.py
"""
from __future__ import annotations

import asyncio
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from sqlalchemy import delete, select

from config import AsyncSessionLocal
from models.database import Movie, User, UserRating
from services.clustering_service import ClusteringService
from services.qdrant_service import QdrantService

from scripts.bench_signal_a_production import ci95
from scripts.bench_synthetic_profiles import PREFIX, build_profiles, cleanup

SPLITS = int(os.environ.get("BENCH_SPLITS", "20"))
K = 40
HIGH_RATING = 4.5
VARIANTES = ("cap55", "sin_cap", "blando", "usuario", "solo_vbs", "blando_raiz")
# solo_vbs es el CONTROL que delata a `blando`: el rango de RRF es estrechísimo
# (1/60..1/100, factor 1.3) frente al de VBS (factor 5), así que el producto rrf·VBS puede
# estar dominado por el VBS y ser "ordena por calidad" disfrazado — o sea canonicidad otra
# vez, el sesgo que ya nos engañó una vez. Si solo_vbs empata con blando, blando no aporta
# nada sobre ordenar por calidad. blando_raiz atenúa el VBS para ver dónde está el equilibrio.


def _ordenar(variante: str, crudo: list[tuple[int, float]], vbs: dict[int, float],
             suelo_usuario: float) -> list[int]:
    """crudo = [(tmdb_id, rrf_score)] en orden de producción."""
    if variante == "cap55":
        return [t for t, _ in crudo if (vbs.get(t) or 0) >= 55][:K]
    if variante == "sin_cap":
        return [t for t, _ in crudo][:K]
    if variante == "blando":
        rank = sorted(crudo, key=lambda x: -(x[1] * ((vbs.get(x[0]) or 0) / 100.0)))
        return [t for t, _ in rank][:K]
    if variante == "solo_vbs":
        return [t for t, _ in sorted(crudo, key=lambda x: -(vbs.get(x[0]) or 0))][:K]
    if variante == "blando_raiz":
        rank = sorted(crudo, key=lambda x: -(x[1] * (((vbs.get(x[0]) or 0) / 100.0) ** 0.5)))
        return [t for t, _ in rank][:K]
    if variante == "usuario":
        return [t for t, _ in crudo if (vbs.get(t) or 0) >= suelo_usuario][:K]
    raise ValueError(variante)


async def _evaluar(user_id: int, split_idx: int, qd: QdrantService, seed_base: int) -> dict:
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(UserRating).where(UserRating.user_id == user_id))).scalars().all()
        loved = [r for r in rows if (r.rating or 0) >= HIGH_RATING]
        if len(loved) < 8:
            return {}
        rnd = random.Random(5000 + split_idx * 31 + seed_base)
        shuf = list(loved)
        rnd.shuffle(shuf)
        held_ids = {r.movie_id for r in shuf[: len(shuf) // 2]}

        u = User(username=f"{PREFIX}qg_{user_id}_{split_idx}",
                 email=f"{PREFIX}qg_{user_id}_{split_idx}@local", country_code="ES")
        db.add(u)
        await db.flush()
        sid = u.id
        for r in rows:
            if r.movie_id in held_ids:
                continue
            db.add(UserRating(user_id=sid, movie_id=r.movie_id, rating=r.rating,
                              is_liked=r.is_liked, is_watched=r.is_watched,
                              watch_count=r.watch_count, watched_date=r.watched_date))
        await db.commit()
        try:
            alcanzables = set((await db.execute(
                select(Movie.tmdb_id).where(Movie.id.in_(list(held_ids)))
                .where(Movie.has_enriched_embedding.is_(True))
            )).scalars().all())

            # Suelo adaptativo: percentil 10 del VBS de lo que ESTE usuario ya ve.
            propios = (await db.execute(
                select(Movie.vectorbox_score)
                .join(UserRating, UserRating.movie_id == Movie.id)
                .where(UserRating.user_id == sid)
                .where(Movie.vectorbox_score.isnot(None))
            )).scalars().all()
            suelo = float(np.percentile(propios, 10)) if propios else 55.0

            recs = await ClusteringService(qdrant=qd).get_user_centric_recommendations(sid, db, limit=500)
            crudo = [(r.get("movie_id"), float(r.get("score") or 0.0)) for r in recs]
            vbs = dict((await db.execute(
                select(Movie.tmdb_id, Movie.vectorbox_score)
                .where(Movie.tmdb_id.in_([t for t, _ in crudo]))
            )).all())

            out = {"_suelo": suelo, "_crudo": len(crudo), "_alcanzables": len(alcanzables)}
            for v in VARIANTES:
                lista = _ordenar(v, crudo, vbs, suelo)
                out[v] = len(set(lista) & alcanzables)
                vals = [vbs.get(t) or 0 for t in lista]
                out[f"vbs_{v}"] = float(np.mean(vals)) if vals else 0.0
                out[f"n_{v}"] = len(lista)
            return out
        finally:
            await db.execute(delete(UserRating).where(UserRating.user_id == sid))
            await db.execute(delete(User).where(User.id == sid))
            await db.commit()


async def main() -> None:
    await cleanup()
    print("Perfiles sintéticos (metadatos, cero vectores):")
    perfiles = await build_profiles()
    objetivos = list(perfiles.items()) + [("REAL 212", 212), ("REAL 210", 210)]
    qd = QdrantService()
    print(f"\n{SPLITS} splits · K={K} · denominador = ocultas con embedding (sin condición de VBS)\n")
    try:
        for nombre, uid in objetivos:
            acc: dict[str, list[float]] = {}
            for sp in range(SPLITS):
                r = await _evaluar(uid, sp, qd, seed_base=abs(uid) % 10000)
                if not r:
                    break
                for k, val in r.items():
                    acc.setdefault(k, []).append(val)
            if not acc:
                continue
            print(f"  {nombre}  (suelo adaptativo medio {np.mean(acc['_suelo']):.0f} · "
                  f"ocultas alcanzables {np.mean(acc['_alcanzables']):.0f})")
            base = np.array(acc["cap55"])
            for v in VARIANTES:
                m, h = ci95(acc[v])
                vq = np.mean(acc[f"vbs_{v}"])
                n = np.mean(acc[f"n_{v}"])
                if v == "cap55":
                    print(f"     {v:8s} aciertos {m:5.2f} ±{h:<4.2f} · VBS lista {vq:5.1f} · n={n:4.1f}")
                else:
                    d, dh = ci95(np.array(acc[v]) - base)
                    marca = "MEJOR" if d - dh > 0 else ("PEOR" if d + dh < 0 else "=")
                    print(f"     {v:8s} aciertos {m:5.2f} ±{h:<4.2f} · VBS lista {vq:5.1f} · n={n:4.1f}"
                          f"  → vs cap55 {d:+.2f} ±{dh:.2f} {marca}")
            print()
    finally:
        n = await cleanup()
        print(f"limpieza: {n} perfiles borrados")


if __name__ == "__main__":
    asyncio.run(main())
