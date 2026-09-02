"""¿En qué espacio debe vivir el centroide de gusto? — hold-out temporal sobre PELÍCULAS.

## Qué decide

Producción promedia vectores en 7 sitios y **ninguno centra** (`clustering_service:931`
el centroide global del usuario, los de K-Means, `_user_center_vector` que alimenta el pool
de joyas ocultas y la cola del radar, `similar.py:83`, el mapa de gustos). Medido el
2026-08-19, el centroide crudo de u210 está a **0,971 del centro del catálogo** — su vector
de gustos es un 97% "esto es una película" — y cambiar de espacio le cambia el **100% del
top-50**.

Lo que NO estaba establecido es **cuál es mejor**, y eso no se decide a ojo: ABTT ganó en
coherencia de directores y su salida parecía peor recuperando gusto de usuario. Este banco
lo mide en la tarea real.

## Diseño

Mismo hold-out temporal que `bench_person_discovery`, sobre películas:

    corte D → centroide con lo amado ANTES de D
            → objetivos = películas amadas DESPUÉS de D y no vistas antes
            → candidatos = catálogo menos lo visto antes

El futuro no entra en la construcción, así que no se puede hacer trampa. La métrica es el
**percentil del objetivo** (más bajo mejor), promediado por objetivo y comparado **pareado**
entre espacios sobre los mismos objetivos — media contra media no vale, unas películas son
intrínsecamente más fáciles que otras.

## Espacios comparados

  · `crudo`      lo que hay en producción hoy
  · `centrado`   α=0.5, lo que usa `mood_axes`
  · `ABTT k=1`   resta la media y elimina la 1ª componente principal (Mu & Viswanath 2018)
  · `ABTT k=7`   el `d/100` que recomienda el paper — medido PEOR aquí en otra tarea
  · `VBS`        control: "recomienda lo mejor puntuado". Sin él no se sabe si el
                 centroide aporta algo o sólo está redescubriendo la calidad.
  · `azar`       ancla obligatoria: debe dar ~50%.

    docker compose exec backend python scripts/bench_vector_space.py
"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from sqlalchemy import text

from config import AsyncSessionLocal
from services.mood_axes import CENTERING_ALPHA, center
from services.qdrant_service import QdrantService
from scripts.compute_mood_axes import load_catalogue
from scripts.bench_signal_a_production import ci95

CORTES = ("2024-01-01", "2025-01-01", "2026-01-01")
MIN_SEMILLAS = 20
TOP_K = (10, 50, 200)


def _unit(M):
    return M / np.linalg.norm(M, axis=1, keepdims=True)


def _abtt(X, k):
    Y = X - X.mean(axis=0)
    _, _, Vt = np.linalg.svd(Y, full_matrices=False)
    for i in range(k):
        Y = Y - np.outer(Y @ Vt[i], Vt[i])
    return _unit(Y)


async def main() -> None:
    qd = QdrantService()
    ids, X = await load_catalogue(qd)
    pos = {t: i for i, t in enumerate(ids)}

    espacios = {
        "crudo": _unit(X),
        "centrado a=0.5": _unit(center(X, CENTERING_ALPHA)),
        "ABTT k=1": _abtt(X, 1),
        "ABTT k=7": _abtt(X, 7),
    }
    async with AsyncSessionLocal() as db:
        vbs = dict((t, float(v or 0)) for t, v in (await db.execute(text(
            "select tmdb_id, vectorbox_score from movies where tmdb_id is not null"
        ))).all())
    control_vbs = np.array([vbs.get(t, 0.0) for t in ids])
    rng = np.random.default_rng(3)
    control_azar = rng.random(len(ids))

    metodos = list(espacios) + ["VBS", "azar"]
    acc = {m: [] for m in metodos}

    async with AsyncSessionLocal() as db:
        for uid in (210, 212):
            for c in CORTES:
                corte = dt.date.fromisoformat(c)
                semillas = [t for t, in (await db.execute(text(
                    """select distinct m.tmdb_id from user_ratings ur join movies m on m.id=ur.movie_id
                       where ur.user_id=:u and ur.watched_date < :c and ur.rating >= 4.0"""
                ), {"u": uid, "c": corte})).all() if t in pos]
                vistos = {t for t, in (await db.execute(text(
                    """select distinct m.tmdb_id from user_ratings ur join movies m on m.id=ur.movie_id
                       where ur.user_id=:u and ur.watched_date < :c"""
                ), {"u": uid, "c": corte})).all()}
                objetivos = [t for t, in (await db.execute(text(
                    """select distinct m.tmdb_id from user_ratings ur join movies m on m.id=ur.movie_id
                       where ur.user_id=:u and ur.watched_date >= :c and ur.rating >= 4.0"""
                ), {"u": uid, "c": corte})).all() if t in pos and t not in vistos]
                if len(semillas) < MIN_SEMILLAS or not objetivos:
                    print(f"u{uid} {c}: descartado ({len(semillas)} semillas, {len(objetivos)} objetivos)")
                    continue

                fuera = [pos[t] for t in vistos if t in pos]
                n_cand = len(ids) - len(fuera)
                print(f"u{uid} {c}: {len(semillas)} semillas, {len(objetivos)} objetivos "
                      f"de {n_cand} candidatos")
                for m in metodos:
                    if m == "VBS":
                        s = control_vbs.copy()
                    elif m == "azar":
                        s = control_azar.copy()
                    else:
                        M = espacios[m]
                        mu = M[[pos[t] for t in semillas]].mean(axis=0)
                        s = M @ (mu / np.linalg.norm(mu))
                    s = s.astype(float)
                    s[fuera] = -np.inf
                    orden = np.argsort(-s)
                    puesto = np.empty(len(ids), int)
                    puesto[orden] = np.arange(1, len(ids) + 1)
                    p = [puesto[pos[t]] for t in objetivos]
                    acc[m] += [x / n_cand for x in p]
                    print(f"   {m:<15} mediana #{int(np.median(p)):<6} "
                          + " ".join(f"top{k}={100*np.mean([x<=k for x in p]):>3.0f}%" for k in TOP_K))
                print()

    if not acc["crudo"]:
        print("sin folds evaluables")
        return

    print("=== AGREGADO — percentil del objetivo (más bajo mejor) ===")
    base = np.array(acc["crudo"])
    for m in metodos:
        v = np.array(acc[m])
        extra = ""
        if m != "crudo":
            d = base - v                       # positivo = mejor que producción
            mu_d, h = ci95(d)
            veredicto = ("MEJOR que producción" if mu_d - h > 0
                         else "PEOR que producción" if mu_d + h < 0 else "no concluyente")
            extra = f"  vs crudo {mu_d*100:+.1f}pp [{(mu_d-h)*100:+.1f}, {(mu_d+h)*100:+.1f}] {veredicto}"
        print(f"   {m:<15} {100*v.mean():>5.1f}%{extra}")
    az = 100 * np.array(acc["azar"]).mean()
    print(f"\nancla: el azar debe dar ~50%. Da {az:.1f}% — "
          f"{'OK' if abs(az - 50) < 8 else 'BANCO ROTO'}")


if __name__ == "__main__":
    asyncio.run(main())
