"""Los 5 ejes: mide si una lista es BUENA Y VARIADA, sin predecir nada.

Por qué no un hold-out: predecir el próximo visionado es otra pregunta. Lo que ves el
domingo lo deciden el ánimo, con quién estás y qué tienes a mano; que te apetezca una de
superhéroes no significa que las 5 del motor fueran peores. Y el hold-out aleatorio premia
la canonicidad (user 210 ha puntuado ≥4.5 a 24 de las 40 mejores del catálogo, así que
«devuelve el canon» le acierta 26 de 30).

Los cinco ejes vienen del banco de grupos (BACKLOG 2026-08-03, «Lo que la métrica NO ve»):

  coherencia   ¿se parece a algo que ya amas?   máx. coseno contra tu biblioteca
  percentil    ¿cómo de arriba está?            en qué percentil del catálogo cae esa coherencia

RETIRADO: «cobertura» como % dentro del top-500 por máxima similitud. Medido dos veces y
mal las dos. Contra el CENTROIDE daba 0% a las tres listas (el centroide de 941 películas
cae en hubs — el motivo por el que la Señal A lo abandonó). Contra la MÁXIMA similitud daba
0% al motor y 10% al azar, siendo que el motor tiene la coherencia MÁS alta: ese top-500 son
casi-clones (Heart of Stone, Ong Bak 2, Bond, Mission: Impossible, Die Another Day para un
perfil Ghibli/arthouse), o sea duplicados de UNA película suelta de la biblioteca, no
afinidad al gusto. Un buen recomendador NO debe estar ahí. El percentil dice lo mismo sin
premiar el clon: el motor cae en la posición ~104 de 20.000, el 0.5% superior.
  no-obviedad  ¿es recomendación o lista?       % que también está en el top-N por puro VBS
  calidad      ¿es bueno?                       VBS medio y % por debajo de 55
  variedad     ¿es variado?                     distancia media entre pares, géneros, décadas

Ninguno necesita hold-out ni tráfico, y **no-obviedad penaliza explícitamente el canon**,
que es el sesgo que invalida las demás métricas que probamos.

Se miden SIEMPRE junto a dos referencias, o los números no significan nada:
  GENÉRICA  las top-N del catálogo por VBS, ciega al usuario
  AZAR      N películas al azar del catálogo enriquecido

Uso:
    docker compose exec backend python scripts/bench_cinco_ejes.py
"""
from __future__ import annotations

import asyncio
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import redis.asyncio as aioredis
from sqlalchemy import select

from config import AsyncSessionLocal, REDIS_URL
from models.database import Movie, UserRating
from services.qdrant_service import QdrantService
from services.recommendation_service import RecommendationService
from services.tmdb_client import TMDBClient

SEED = 99
TOP500 = 500


def _norm(v):
    v = np.asarray(v, dtype=np.float32)
    n = float(np.linalg.norm(v))
    return v / n if n else v


async def _ejes(ids, vecs, meta, lib_vecs, orbita, genericas):
    """Devuelve los 5 ejes para una lista de tmdb_ids."""
    usables = [t for t in ids if t in vecs]
    if not usables:
        return None
    V = np.stack([_norm(vecs[t]) for t in usables])

    # coherencia: máximo coseno contra la biblioteca del usuario
    coh = float(np.mean(np.max(V @ lib_vecs.T, axis=1))) if lib_vecs.size else 0.0
    # percentil: dónde cae esa coherencia en la distribución del catálogo entero
    pct = 100.0 * float(np.mean(orbita < coh)) if orbita.size else 0.0
    # no-obviedad: cuántas están TAMBIÉN en la lista genérica (menos es mejor)
    obv = 100.0 * len([t for t in ids if t in genericas]) / max(1, len(ids))
    # calidad
    vbs = [meta[t]["vbs"] for t in ids if t in meta and meta[t]["vbs"] is not None]
    q = float(np.mean(vbs)) if vbs else 0.0
    bajo = 100.0 * len([v for v in vbs if v < 55]) / max(1, len(vbs))
    # variedad: distancia media entre pares + géneros y décadas distintas
    sim = V @ V.T
    n = len(usables)
    ild = float((n * n - np.sum(sim)) / (n * (n - 1))) if n > 1 else 0.0
    gen = {g for t in ids if t in meta for g in (meta[t]["genres"] or [])}
    dec = {(meta[t]["year"] // 10) * 10 for t in ids if t in meta and meta[t]["year"]}
    return {"coherencia": coh, "percentil": pct, "obviedad": obv, "vbs": q,
            "pct_bajo55": bajo, "variedad": ild, "generos": len(gen), "decadas": len(dec)}


async def main() -> None:
    qd = QdrantService()
    rnd = random.Random(SEED)
    r = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        async with AsyncSessionLocal() as db:
            catalogo = (await db.execute(
                select(Movie.tmdb_id, Movie.vectorbox_score, Movie.genres, Movie.year)
                .where(Movie.has_enriched_embedding.is_(True))
                .where(Movie.tmdb_id.isnot(None))
            )).all()
        meta = {t: {"vbs": v, "genres": g, "year": y} for t, v, g, y in catalogo}
        todos = [t for t, *_ in catalogo]

        for uid in (212, 210):
            async with AsyncSessionLocal() as db:
                lib_ids = (await db.execute(
                    select(Movie.tmdb_id)
                    .join(UserRating, UserRating.movie_id == Movie.id)
                    .where(UserRating.user_id == uid)
                    .where(Movie.has_enriched_embedding.is_(True))
                )).scalars().all()
                vistas = set(lib_ids)
                async for k in r.scan_iter(match=f"signal_cache:{uid}:*", count=100):
                    await r.delete(k)
                rs = RecommendationService(db, tmdb=TMDBClient(), qdrant=qd, redis_client=r)
                sec = await rs.get_hybrid_picks_section(uid, "ES", set())
            reco = [i.id for i in sec.items]
            N = len(reco)

            lib_map = await qd.get_vectors_batch(list(lib_ids))
            lib_vecs = np.stack([_norm(v) for v in lib_map.values()]) if lib_map else np.empty((0, 768))
            centro = _norm(lib_vecs.mean(axis=0)) if lib_vecs.size else None

            # Órbita = top-500 del catálogo por MÁXIMA similitud a cualquier película de
            # tu biblioteca. NO por afinidad al centroide: se midió el 2026-08-11 y los
            # picks del motor caen en las posiciones 4094, 9627, 12537 de 20255 por esa
            # vara, dando 0% a las tres listas. No era un bug — el centroide de 941
            # películas cae en tierra de nadie (hubs), que es exactamente el motivo por el
            # que la Señal A dejó de usarlo. Medir contra él es medir el acuerdo con un
            # enfoque abandonado.
            libres = [t for t in todos if t not in vistas]
            cand_map = await qd.get_vectors_batch(libres)
            cand_ids = list(cand_map)
            C = np.stack([_norm(cand_map[t]) for t in cand_ids])
            mejor = np.full(len(cand_ids), -1.0, dtype=np.float32)
            for i in range(0, len(lib_vecs), 512):   # a trozos: 20k x 2.7k no cabe de una
                mejor = np.maximum(mejor, (C @ lib_vecs[i:i + 512].T).max(axis=1))
            orbita = mejor  # distribución completa, para situar cada lista por percentil

            genericas = {t for t in sorted(
                (t for t in libres if meta[t]["vbs"] is not None),
                key=lambda t: -meta[t]["vbs"])[:N]}
            azar = rnd.sample(libres, N)

            listas = {"MOTOR (picked for you)": reco,
                      "GENÉRICA (top VBS)": list(genericas),
                      "AZAR": azar}
            vecs = dict(cand_map)
            for t in reco:
                if t not in vecs:
                    v = await qd.get_vector(t)
                    if v:
                        vecs[t] = v

            print(f"\n===== USER {uid} · N={N} · biblioteca {len(lib_ids)} películas =====")
            print(f"  {'lista':24s} | {'coher.':>7} | {'pctil':>7} | {'obvied.':>8} | "
                  f"{'VBS':>5} | {'%<55':>5} | {'varied.':>7} | {'gén':>4} | {'déc':>4}")
            for nombre, ids in listas.items():
                e = await _ejes(ids, vecs, meta, lib_vecs, orbita, genericas)
                if not e:
                    continue
                print(f"  {nombre:24s} | {e['coherencia']:7.3f} | {e['percentil']:6.1f}% | "
                      f"{e['obviedad']:7.0f}% | {e['vbs']:5.1f} | {e['pct_bajo55']:4.0f}% | "
                      f"{e['variedad']:7.3f} | {e['generos']:4d} | {e['decadas']:4d}")
    finally:
        await r.close()


if __name__ == "__main__":
    asyncio.run(main())
