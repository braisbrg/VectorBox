"""Perfiles sintéticos para evaluar la Señal A sin el sesgo canónico.

Por qué: users 210 y 212 tienen gusto CANÓNICO (210 ha puntuado ≥4.5 a 24 de las 40
mejores películas del catálogo), así que el ancla «devuelve el canon» los acierta por
construcción y aplasta cualquier medida. Con n=2 y ambos sesgados igual, no se puede
saber si la Señal A personaliza para gustos que NO son el canon.

Cómo se evita la circularidad — el error que haría todo esto inútil: los perfiles se
construyen SÓLO con metadatos estructurados (director, género, década, VBS), **nunca
con vecinos vectoriales**. Si se armaran con el propio espacio de embeddings que se está
evaluando, el recomendador recuperaría las ocultas por definición y saldría un falso
positivo perfecto. `AGENTS.md`/CLAUDE.md ya fijan que director y colección son señales
estructuradas y NO entran en el embedding, así que son ejes independientes de lo medido.

Perfiles (todos con nota 5.0, sin `watched_date` para que el decay sea uniforme):
  syn_canon      CONTROL POSITIVO: las mejores por VBS. El ancla canónica DEBE ganar aquí;
                 si no gana, el banco está roto.
  syn_niche      CONTROL NEGATIVO: VBS 55-70 — pasan el gate pero no son canon. El ancla
                 canónica debe sacar ~0, así que sólo se puede puntuar personalizando.
  syn_horror     género Horror, 1975-1999
  syn_docs       género Documentary
  syn_scifi      género Science Fiction, ≥2000
  syn_auteur     por DIRECTOR: filmografías de un puñado de autores

Uso:
    docker compose exec backend python scripts/bench_synthetic_profiles.py
"""
from __future__ import annotations

import asyncio
import os
import random
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from sqlalchemy import cast, delete, select, String

from config import AsyncSessionLocal
from models.database import Movie, User, UserRating
from services.qdrant_service import QdrantService

from scripts.bench_signal_a_production import _run_split, ci95

PREFIX = "__syn__"
N_FILMS = 60
SPLITS = int(os.environ.get("BENCH_SPLITS", "20"))
SEED = 4242

AUTORES = ["Akira Kurosawa", "Ingmar Bergman", "Federico Fellini", "Andrei Tarkovsky",
           "Robert Bresson", "Yasujirō Ozu", "Michelangelo Antonioni", "Werner Herzog"]


async def _pick(db, where_clauses, order_random: bool, n: int, rnd: random.Random) -> list[int]:
    q = select(Movie.id).where(Movie.has_enriched_embedding.is_(True))
    for c in where_clauses:
        q = q.where(c)
    if not order_random:
        q = q.order_by(Movie.vectorbox_score.desc().nullslast()).limit(n)
        return list((await db.execute(q)).scalars().all())
    # ORDER BY explícito: sin él Postgres no garantiza el orden de filas, así que el
    # shuffle sembrado barajaba una lista distinta en cada ejecución y los perfiles NO
    # eran reproducibles (syn_scifi saltó de 1.10 a 0.10 aciertos entre dos corridas).
    ids = list((await db.execute(q.order_by(Movie.id).limit(4000))).scalars().all())
    rnd.shuffle(ids)
    return ids[:n]


async def build_profiles() -> dict[str, int]:
    """Crea los perfiles. Devuelve {nombre: user_id}."""
    rnd = random.Random(SEED)
    created: dict[str, int] = {}
    async with AsyncSessionLocal() as db:
        recetas = {
            "syn_canon":  ([Movie.vectorbox_score.isnot(None)], False),
            "syn_niche":  ([Movie.vectorbox_score >= 55, Movie.vectorbox_score <= 70], True),
            "syn_horror": ([cast(Movie.genres, String).like("%Horror%"),
                            Movie.year >= 1975, Movie.year <= 1999], True),
            "syn_docs":   ([cast(Movie.genres, String).like("%Documentary%")], True),
            "syn_scifi":  ([cast(Movie.genres, String).like("%Science Fiction%"),
                            Movie.year >= 2000], True),
            "syn_auteur": ([cast(Movie.directors, String).op("~")("|".join(AUTORES))], True),
            # ÉPOCA — añadidos 2026-08-19. El diagnóstico de esa fecha fue que la fila
            # sirve cine de 1985 mientras u212 ama cine de 2016, y que con 2 usuarios
            # reales no se podía separar "falla con este usuario" de "falla con este
            # GUSTO". Estos dos lo separan: mismo motor, misma calidad, sólo cambia la
            # época, y son metadato estructurado (`year`) así que no hay circularidad.
            "syn_moderno": ([Movie.year >= 2015], True),
            "syn_clasico": ([Movie.year <= 1975], True),
        }
        for nombre, (clauses, aleatorio) in recetas.items():
            movie_ids = await _pick(db, clauses, aleatorio, N_FILMS, rnd)
            if len(movie_ids) < 20:
                print(f"  {nombre}: sólo {len(movie_ids)} películas — saltado")
                continue
            u = User(username=f"{PREFIX}{nombre}", email=f"{PREFIX}{nombre}@local", country_code="ES")
            db.add(u)
            await db.flush()
            for mid in movie_ids:
                db.add(UserRating(user_id=u.id, movie_id=mid, rating=5.0,
                                  is_liked=True, is_watched=True, watch_count=1))
            created[nombre] = u.id
            print(f"  {nombre}: {len(movie_ids)} películas (user {u.id})")
        await db.commit()
    return created


async def cleanup() -> int:
    async with AsyncSessionLocal() as db:
        ids = (await db.execute(select(User.id).where(User.username.startswith(PREFIX, autoescape=True)))).scalars().all()
        for uid in ids:
            await db.execute(delete(UserRating).where(UserRating.user_id == uid))
            await db.execute(delete(User).where(User.id == uid))
        await db.commit()
        return len(ids)


async def main() -> None:
    borrados = await cleanup()
    if borrados:
        print(f"(limpiados {borrados} perfiles sintéticos previos)")
    print("Construyendo perfiles (sólo metadatos, cero vectores):")
    perfiles = await build_profiles()
    qd = QdrantService()
    print(f"\n{SPLITS} splits · K = tamaño real de la lista de producción\n")
    print(f"  {'perfil':12s} | {'PRODUCCIÓN':>12s} | {'emparejado':>12s} | {'canon':>12s} | "
          f"{'prod−empar.':>14s}")
    try:
        for nombre, uid in perfiles.items():
            acc: dict[str, list[int]] = {}
            for sp in range(SPLITS):
                r = await _run_split(uid, sp, qd, seed_base=zlib.crc32(nombre.encode()) % 10000)
                if not r:
                    break
                for k in ("PRODUCCIÓN", "CONTROL VBS-emparejado", "ANCLA canon"):
                    acc.setdefault(k, []).append(r[k])
            if not acc:
                print(f"  {nombre:12s} | (sin datos suficientes)")
                continue
            p, ph = ci95(acc["PRODUCCIÓN"])
            e, eh = ci95(acc["CONTROL VBS-emparejado"])
            c, ch = ci95(acc["ANCLA canon"])
            d, dh = ci95(np.array(acc["PRODUCCIÓN"]) - np.array(acc["CONTROL VBS-emparejado"]))
            marca = "SÍ" if d - dh > 0 else ("no" if d + dh < 0 else "—")
            print(f"  {nombre:12s} | {p:6.2f} ±{ph:<5.2f} | {e:6.2f} ±{eh:<5.2f} | "
                  f"{c:6.2f} ±{ch:<5.2f} | {d:+6.2f} ±{dh:<5.2f} {marca}")
    finally:
        n = await cleanup()
        print(f"\nlimpieza: {n} perfiles sintéticos borrados")


if __name__ == "__main__":
    asyncio.run(main())
