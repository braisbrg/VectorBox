"""Held-out para Señal A, CON ancla y con ruido — re-chequeo de A vs G2.

`experiment_signal_a_heldout.py` (2026-05) decidió que G2 fuera a producción contando
aciertos hold-out y declarando ganador por conteo bruto. Tenía dos agujeros:

  1. **Sin ancla.** Nada respondía «¿cuánto saca una lista que ignora al usuario?».
     Medido en Señal C el 2026-08-06: devolver las 30 películas más canónicas del
     catálogo recupera 26 de 30 favoritas ocultas de user 210. Una métrica que un
     recomendador ciego gana no mide personalización, mide presencia de canon.
  2. **Un solo split** (`SEED = 42`). Sin repetición no hay ruido, y sin ruido no se
     puede decir si una diferencia existe.

Este script añade las dos cosas y estratifica: los aciertos sobre ocultas canónicas
(VBS ≥ CANON_VBS) son los que el control puede robar; los de por debajo son los únicos
donde se está midiendo personalizar de verdad.

NO reemplaza al original — el original se conserva como registro de cómo se decidió.

Uso:
    docker compose exec backend python scripts/experiment_signal_a_heldout_anchored.py
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
from models.database import Movie
from services.qdrant_service import QdrantService

from scripts.experiment_signal_a_heldout import (
    TOP_K,
    MIN_HIGH,
    _all_watched_tmdb_ids,
    _get_high_rated_with_vectors,
    _strategy_a_topk,
    _strategy_g2_topk,
)

USERS = [210, 212]
SPLITS = 20          # splits nuevos; el original usaba UNO
SPLIT_SEED = 3000    # distinto del SEED=42 original: casos que no eligieron al ganador
CANON_VBS = 85.0     # umbral de "canónica" para estratificar


def ci95(values) -> tuple[float, float]:
    a = np.array(values, dtype=float)
    if len(a) < 2:
        return float(a.mean()), 0.0
    return float(a.mean()), float(1.96 * a.std(ddof=1) / np.sqrt(len(a)))


async def _canon_top(exclude: set[int], top_k: int) -> list[int]:
    """ANCLA: las top-K del catálogo por VBS. No mira al usuario en absoluto."""
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(Movie.tmdb_id)
            .where(Movie.has_enriched_embedding.is_(True))
            .where(Movie.tmdb_id.isnot(None))
            .order_by(Movie.vectorbox_score.desc().nullslast())
            .limit(top_k + len(exclude) + 500)
        )).scalars().all()
    return [t for t in rows if t not in exclude][:top_k]


async def _vbs_of(tmdb_ids: set[int]) -> dict[int, float]:
    if not tmdb_ids:
        return {}
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(Movie.tmdb_id, Movie.vectorbox_score).where(Movie.tmdb_id.in_(list(tmdb_ids)))
        )).all()
    return {t: (v or 0.0) for t, v in rows}


async def main() -> None:
    qd = QdrantService()
    print(f"K={TOP_K} · {SPLITS} splits · canónica = VBS ≥ {CANON_VBS}\n")

    for user_id in USERS:
        high_rated = await _get_high_rated_with_vectors(user_id, qd)
        if len(high_rated) < MIN_HIGH:
            print(f"user {user_id}: saltado ({len(high_rated)} películas ≥4.5 con vector)")
            continue
        all_watched = await _all_watched_tmdb_ids(user_id)

        res: dict[str, list[tuple[int, int, int]]] = {}
        for sp in range(SPLITS):
            rng = random.Random(SPLIT_SEED + sp + user_id)
            shuffled = list(high_rated)
            rng.shuffle(shuffled)
            half = len(shuffled) // 2
            anchor_vectors = [v for _, v in shuffled[:half]]
            held_out = {tid for tid, _ in shuffled[half:]}
            exclude = all_watched - held_out

            vbs = await _vbs_of(held_out)
            hi = {t for t in held_out if vbs.get(t, 0.0) >= CANON_VBS}
            lo = held_out - hi

            lists = {
                "A (centroide)": await _strategy_a_topk(qd, anchor_vectors, exclude, TOP_K),
                "G2 (consenso)": await _strategy_g2_topk(qd, anchor_vectors, exclude, TOP_K),
                "ANCLA solo-canon": await _canon_top(exclude, TOP_K),
            }
            for name, lst in lists.items():
                s = set(lst)
                res.setdefault(name, []).append((len(s & held_out), len(s & hi), len(s & lo)))

        print(f"===== USER {user_id} — {len(high_rated)} películas ≥4.5 con vector =====")
        print(f"  {'variante':18s} | {'aciertos':>16s} | {'canónicas':>15s} | {'NO canónicas':>15s}")
        for name in ("ANCLA solo-canon", "A (centroide)", "G2 (consenso)"):
            arr = np.array(res[name])
            t, th = ci95(arr[:, 0]); h, hh = ci95(arr[:, 1]); l, lh = ci95(arr[:, 2])
            print(f"  {name:18s} | {t:6.2f} ±{th:<8.2f} | {h:5.2f} ±{hh:<7.2f} | {l:5.2f} ±{lh:<7.2f}")

        a, g = np.array(res["A (centroide)"]), np.array(res["G2 (consenso)"])
        for label, col in (("totales", 0), ("solo NO canónicas", 2)):
            m, h = ci95(g[:, col] - a[:, col])
            verdict = "SIGNIFICATIVO" if abs(m) > h else "no significativo"
            print(f"  --> pareado G2 − A ({label}): {m:+.2f} ±{h:.2f}  {verdict}")
        anchor_arr = np.array(res["ANCLA solo-canon"])
        m, h = ci95(g[:, 0] - anchor_arr[:, 0])
        print(f"  --> pareado G2 − ANCLA (totales): {m:+.2f} ±{h:.2f}  "
              f"{'G2 gana' if m - h > 0 else 'el ancla NO es batida'}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
