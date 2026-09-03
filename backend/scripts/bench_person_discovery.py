"""¿Te habría llevado a esa persona ANTES de que llegaras solo? — hold-out TEMPORAL.

## Por qué existe, si ya hay `bench_inter_director.py`

Aquél esconde una persona que el usuario YA ama y mira dónde cae en el ranking. Suena
razonable y **no sirve para decidir**, porque las personas que has visto son las famosas:
la métrica premia alinearse con la popularidad. Medido el 2026-08-19, prefería la variante
(media de centroides) cuya salida real era visiblemente peor — Len Wiseman y *Mean Girls*
para un usuario que ama a Fritz Lang — frente a la que daba Dmytryk, Goulding y Pakula.

Una métrica que puntúa RECUPERACIÓN no puede evaluar DESCUBRIMIENTO. Ésta sí:

    corte D → señal construida SOLO con lo visto antes de D
            → objetivos = personas que el usuario empezó a amar DESPUÉS de D
                          y que no había visto antes

Eso es literalmente la promesa del producto: llevarte a alguien al que ibas a llegar, pero
antes. Y no se puede hacer trampa, porque el futuro no entra en la construcción.

## Sesgos conocidos, declarados

  · **`vote_count` es de HOY**, así que el control de popularidad ve un poco de futuro.
    Se acepta a propósito: es el control, y ser generoso con él hace la prueba más dura
    para lo que queremos validar, no más fácil.
  · **Sólo dos usuarios tienen `watched_date`** (210 y 212). Los perfiles sintéticos no
    sirven aquí: no tienen historia temporal. Es la limitación de fondo de este banco y
    por eso se reportan los folds POR CONFIGURACIÓN, nunca sólo la media.
  · Un objetivo no alcanzado no significa mala recomendación — puede ser una persona a la
    que se llegó por un camino que ninguna señal de afinidad podía prever.

## Variantes que compara

  · `azar`        ancla obligatoria: debe dar ~n/2. Si no, el banco está roto.
  · `fama`        el control que duele: "recomienda famosos" acierta mucho sin saber nada.
  · `media`       un centroide con todas tus personas. Colapsa si tienes muchas.
  · `RRF`         vecinos de CADA persona, fusionados por rango. No promedia vectores.
  · `banda+X`     lo anterior manteniendo el orden de fama por bandas y ordenando dentro.

Espacio: **ABTT k=1** (Mu & Viswanath 2018). Medido, separa 7,0× el ruido frente a 2,1×
del centrado α=0.5, y k=7 (el `d/100` del paper) es PEOR aquí porque este espacio tiene
UNA dirección dominante.

    docker compose exec backend python scripts/bench_person_discovery.py
    docker compose exec backend python scripts/bench_person_discovery.py --actores
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from sqlalchemy import text

from config import AsyncSessionLocal
from services.qdrant_service import QdrantService
from scripts.compute_mood_axes import load_catalogue
from scripts.bench_signal_a_production import ci95

K_RRF = 60
BANDA = 200
CORTES = ("2024-01-01", "2025-01-01", "2026-01-01")
MIN_SEMILLAS = 8          # con menos personas antes del corte no hay señal que construir
TOP_K = (10, 25, 50)


async def _espacio_y_personas(columna: str, min_films: int):
    """(nombres, matriz de centroides ABTT, fama) para directores o para reparto."""
    qd = QdrantService()
    ids, X = await load_catalogue(qd)
    pos = {t: i for i, t in enumerate(ids)}
    Y = X - X.mean(axis=0)
    _, _, Vt = np.linalg.svd(Y, full_matrices=False)
    Y = Y - np.outer(Y @ Vt[0], Vt[0])
    Xc = Y / np.linalg.norm(Y, axis=1, keepdims=True)

    async with AsyncSessionLocal() as db:
        filas = (await db.execute(text(
            f"""select unnest({columna}) p, array_agg(tmdb_id) t from movies
                where {columna} is not null group by 1 having count(*) >= :n"""
        ), {"n": min_films})).all()
        fama = dict((p, n) for p, n in (await db.execute(text(
            f"""select unnest({columna}) p, sum(vote_count) n from movies
                where {columna} is not null group by 1"""
        ))).all())

    nombres, cents = [], []
    for p, tids in filas:
        idx = [pos[t] for t in tids if t in pos]
        if len(idx) < min_films:
            continue
        mu = Xc[idx].mean(axis=0)
        nombres.append(p)
        cents.append(mu / np.linalg.norm(mu))
    return nombres, np.stack(cents), fama


async def _fold(db, uid: int, corte: dt.date, columna: str):
    """(semillas antes del corte, objetivos nuevos amados después)."""
    antes = [p for p, in (await db.execute(text(
        f"""select distinct unnest(m.{columna}) from user_ratings ur
            join movies m on m.id = ur.movie_id
            where ur.user_id = :u and ur.watched_date < :c and ur.rating >= 4.0"""
    ), {"u": uid, "c": corte})).all()]
    vistos = {p for p, in (await db.execute(text(
        f"""select distinct unnest(m.{columna}) from user_ratings ur
            join movies m on m.id = ur.movie_id
            where ur.user_id = :u and ur.watched_date < :c"""
    ), {"u": uid, "c": corte})).all()}
    despues = [p for p, in (await db.execute(text(
        f"""select distinct unnest(m.{columna}) from user_ratings ur
            join movies m on m.id = ur.movie_id
            where ur.user_id = :u and ur.watched_date >= :c and ur.rating >= 4.0"""
    ), {"u": uid, "c": corte})).all()]
    return antes, vistos, [p for p in despues if p not in vistos]


def _rankings(C, nombres, idx, pop, semillas, vistos, banda, rng):
    """Cada variante devuelve un array de puntuación sobre todos los candidatos."""
    fuera = [i for i, n in enumerate(nombres) if n in vistos]
    S = C[[idx[p] for p in semillas]]

    mu = S.mean(axis=0)
    media = C @ (mu / np.linalg.norm(mu))

    rrf = np.zeros(len(nombres))
    for p in semillas:
        s = C @ C[idx[p]]
        for r, i in enumerate([i for i in np.argsort(-s) if i not in set(fuera)][:100]):
            rrf[i] += 1.0 / (K_RRF + r + 1)

    salidas = {"media": media, "RRF": rrf, "fama": pop.astype(float).copy(),
               "azar": rng.random(len(nombres))}
    for k in list(salidas):
        salidas[k] = salidas[k].copy()
        salidas[k][fuera] = -np.inf
    # Variantes con banda: orden de fama por banda, y dentro por la señal.
    for base in ("media", "RRF"):
        orden = np.lexsort((-salidas[base], banda))
        puesto = np.empty(len(nombres), float)
        puesto[orden] = -np.arange(len(nombres))
        puesto[fuera] = -np.inf
        salidas[f"banda+{base}"] = puesto
    return salidas


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--actores", action="store_true", help="usa `cast` en vez de `directors`")
    ap.add_argument("--min-films", type=int, default=4)
    args = ap.parse_args()

    columna = "cast" if args.actores else "directors"
    etiqueta = "ACTORES" if args.actores else "DIRECTORES"
    nombres, C, fama = await _espacio_y_personas(f'"{columna}"' if args.actores else columna,
                                                 args.min_films)
    idx = {p: i for i, p in enumerate(nombres)}
    pop = np.array([fama.get(p, 0) for p in nombres], dtype=float)
    orden_fama = np.argsort(-pop)
    banda = np.empty(len(nombres), int)
    for puesto, i in enumerate(orden_fama):
        banda[i] = puesto // BANDA
    rng = np.random.default_rng(11)

    print(f"=== {etiqueta} · {len(nombres)} personas con >={args.min_films} películas ===\n")

    metodos = ["azar", "fama", "media", "RRF", "banda+media", "banda+RRF"]
    acumulado = defaultdict(list)      # método -> [percentil de cada objetivo]
    async with AsyncSessionLocal() as db:
        for uid in (210, 212):
            for c in CORTES:
                corte = dt.date.fromisoformat(c)
                semillas, vistos, objetivos = await _fold(db, uid, corte, columna)
                semillas = [p for p in semillas if p in idx]
                objetivos = [p for p in objetivos if p in idx]
                if len(semillas) < MIN_SEMILLAS or not objetivos:
                    print(f"u{uid} corte {c}: descartado "
                          f"({len(semillas)} semillas, {len(objetivos)} objetivos)")
                    continue
                sal = _rankings(C, nombres, idx, pop, semillas, vistos, banda, rng)
                n_cand = sum(1 for n in nombres if n not in vistos)
                linea = [f"u{uid} {c}: {len(semillas):>4} semillas, "
                         f"{len(objetivos):>3} objetivos de {n_cand} candidatos"]
                for m in metodos:
                    ordenados = [nombres[i] for i in np.argsort(-sal[m])][:n_cand]
                    puestos = [ordenados.index(o) + 1 for o in objetivos if o in ordenados]
                    acumulado[m] += [p / n_cand for p in puestos]
                    linea.append(f"   {m:<12} mediana #{int(np.median(puestos)):<5} "
                                 + " ".join(f"top{k}={100*np.mean([p<=k for p in puestos]):>3.0f}%"
                                            for k in TOP_K))
                print("\n".join(linea) + "\n")

    if not acumulado:
        print("sin folds evaluables")
        return

    print("=== AGREGADO (percentil del objetivo, más bajo es mejor) ===")
    base = np.array(acumulado["fama"])
    for m in metodos:
        v = np.array(acumulado[m])
        extra = ""
        if m != "fama":
            d = base - v                      # positivo = mejor que la fama
            mu_d, h = ci95(d)
            veredicto = ("MEJOR que fama" if mu_d - h > 0
                         else "peor que fama" if mu_d + h < 0 else "no concluyente")
            extra = f"  vs fama {mu_d*100:+.1f}pp [{(mu_d-h)*100:+.1f}, {(mu_d+h)*100:+.1f}] {veredicto}"
        print(f"   {m:<12} percentil medio {100*v.mean():>5.1f}%{extra}")
    az = 100 * np.array(acumulado["azar"]).mean()
    print(f"\nancla: el azar debe dar ~50%. Da {az:.1f}% — "
          f"{'OK' if abs(az - 50) < 8 else 'BANCO ROTO'}")


if __name__ == "__main__":
    asyncio.run(main())
