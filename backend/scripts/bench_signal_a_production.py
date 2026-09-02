"""Banco hold-out de la Señal A que evalúa LA FUNCIÓN DE PRODUCCIÓN.

Por qué existe: `experiment_signal_a_heldout.py` compara dos *reimplementaciones* de las
estrategias, no el código que ships. Medido 2026-08-06, su `_strategy_g2_topk` devolvía
1 y 0 películas (exige consenso de ≥2 anchors, que en este espacio casi no ocurre), así
que su veredicto «A vs G2» se decidía entre una lista de 40 y una vacía. La Señal A real
añade detrás los de un solo anchor y devuelve ~139.

Método — usuario SOMBRA, que es lo que permite probar producción sin tocarla:
  1. Se elige la mitad de las películas queridas del usuario y se OCULTA.
  2. Se crea un usuario temporal con TODAS sus valoraciones menos esas — o sea, el perfil
     real tal y como estaba antes de registrarlas.
  3. Se llama a `ClusteringService.get_user_centric_recommendations` sin trampas.
  4. Se cuenta cuántas ocultas aparecen. El sombra se borra siempre.

Anclas obligatorias (sin ellas el número no significa nada, ver CLAUDE.md regla 3):
  - CANON: top-K del catálogo por VBS, ignorando al usuario.
  - AZAR:  K películas al azar del catálogo enriquecido.

Uso:
    docker compose exec backend python scripts/bench_signal_a_production.py
"""
from __future__ import annotations

import asyncio
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from sqlalchemy import delete, func, select

from config import AsyncSessionLocal
from models.database import Movie, User, UserRating
from services.clustering_service import ClusteringService
from services.recommendation_service import MIN_QUALITY_SCORE
from services.qdrant_service import QdrantService

USERS = [212, 210]
SPLITS = int(os.environ.get('BENCH_SPLITS', '8'))
K = int(os.environ.get('BENCH_K', '40'))
HIGH_RATING = 4.5
SHADOW_PREFIX = "__bench_shadow__"


def ci95(values) -> tuple[float, float]:
    a = np.array(values, dtype=float)
    if len(a) < 2:
        return float(a.mean()), 0.0
    return float(a.mean()), float(1.96 * a.std(ddof=1) / np.sqrt(len(a)))


async def _cleanup_shadows() -> int:
    """Borra cualquier sombra huérfana de una ejecución anterior interrumpida.

    `startswith(..., autoescape=True)`, NUNCA `like(f"{PREFIX}%")`: en SQL LIKE el guion
    bajo es un COMODÍN de un carácter, así que `"__bench_shadow__%"` casa con mucho más de
    lo que parece — y esta función BORRA usuarios. Con el patrón sin escapar, un prefijo
    como `"__"` selecciona a cualquier usuario de dos o más caracteres, los reales incluidos.
    """
    async with AsyncSessionLocal() as db:
        ids = (await db.execute(
            select(User.id).where(User.username.startswith(SHADOW_PREFIX, autoescape=True))
        )).scalars().all()
        for sid in ids:
            await db.execute(delete(UserRating).where(UserRating.user_id == sid))
            await db.execute(delete(User).where(User.id == sid))
        await db.commit()
        return len(ids)


async def _run_split(src_user: int, split_idx: int, qd: QdrantService,
                     seed_base: int | None = None) -> dict:
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(UserRating).where(UserRating.user_id == src_user)
        )).scalars().all()
        loved = [r for r in rows if (r.rating or 0) >= HIGH_RATING]
        if len(loved) < 8:
            return {}

        # seed_base permite splits estables cuando el user_id es autoincremental
        # (perfiles sintéticos recreados): si no, cada corrida evalúa otros splits.
        rnd = random.Random(5000 + split_idx * 31 + (seed_base if seed_base is not None else src_user))
        shuffled = list(loved)
        rnd.shuffle(shuffled)
        held_movie_ids = {r.movie_id for r in shuffled[: len(shuffled) // 2]}

        shadow = User(
            username=f"{SHADOW_PREFIX}{src_user}_{split_idx}",
            email=f"{SHADOW_PREFIX}{src_user}_{split_idx}@local",
            country_code="ES",
        )
        db.add(shadow)
        await db.flush()
        sid = shadow.id
        for r in rows:
            if r.movie_id in held_movie_ids:
                continue
            db.add(UserRating(
                user_id=sid, movie_id=r.movie_id, rating=r.rating,
                is_liked=r.is_liked, is_watched=r.is_watched,
                watch_count=r.watch_count, watched_date=r.watched_date,
            ))
        await db.commit()

        try:
            held_tmdb = set((await db.execute(
                select(Movie.tmdb_id).where(Movie.id.in_(list(held_movie_ids)))
            )).scalars().all())
            # Sólo cuentan las que PODRÍAN devolverse: sin vector enriquecido son
            # invisibles para cualquier estrategia y contarlas hunde a todas por igual.
            # ALCANZABLES DE VERDAD: además del vector, tienen que pasar el gate de calidad
            # que el llamador aplica (MIN_QUALITY_SCORE). Una película oculta con VBS 50 no
            # puede aparecer en el feed por definición, así que contarla como fallo mide el
            # gate, no la Señal A. Detectado con un perfil de horror 1975-99 (VBS medio 50.3):
            # producción devolvía 136 candidatos de horror coherentes y el banco marcaba 0.00.
            reachable = set((await db.execute(
                select(Movie.tmdb_id)
                .where(Movie.id.in_(list(held_movie_ids)))
                .where(Movie.has_enriched_embedding.is_(True))
                .where(Movie.vectorbox_score >= MIN_QUALITY_SCORE)
            )).scalars().all())

            cs = ClusteringService(qdrant=qd)
            recs = await cs.get_user_centric_recommendations(sid, db, limit=K)
            raw = [r.get("movie_id") for r in recs]
            # La Señal A cruda NO es lo que ve el usuario: su cabeza va poblada de películas
            # de VBS bajo (Untitled Fairy Tale Project sin VBS, Paheli 51.2, A Burning Hot
            # Summer 48.7 — medido 2026-08-06) y el llamador las corta con MIN_QUALITY_SCORE.
            # Evaluar el crudo mide un intermedio que nadie consume y hunde a producción
            # frente a un ancla que por construcción es toda de VBS alto.
            gated = (await db.execute(
                select(Movie.tmdb_id)
                .where(Movie.tmdb_id.in_(raw))
                .where(Movie.vectorbox_score >= MIN_QUALITY_SCORE)
            )).scalars().all()
            keep = set(gated)
            prod = [t for t in raw if t in keep][:K]
            prod_raw = raw[:K]

            watched = set((await db.execute(
                select(Movie.tmdb_id)
                .join(UserRating, UserRating.movie_id == Movie.id)
                .where(UserRating.user_id == sid)
            )).scalars().all())

            canon = (await db.execute(
                select(Movie.tmdb_id)
                .where(Movie.has_enriched_embedding.is_(True))
                .order_by(Movie.vectorbox_score.desc().nullslast())
                .limit(K + len(watched) + 500)
            )).scalars().all()
            canon = [t for t in canon if t not in watched]

            pool_rows = (await db.execute(
                select(Movie.tmdb_id, Movie.vectorbox_score)
                .where(Movie.has_enriched_embedding.is_(True))
                .where(Movie.vectorbox_score.isnot(None))
            )).all()
            pool_free = [(t, v) for t, v in pool_rows if t not in watched]
            azar_full = [t for t, _ in rnd.sample(pool_free, min(len(pool_free), K))]

            # CONTROL EMPAREJADO POR VBS — el que de verdad aísla la personalización.
            # El ancla canónica gana por construcción cuando el gusto del usuario ES el canon
            # (user 210: ha puntuado ≥4.5 a 24 de las 40 mejores del catálogo), así que un
            # "mira, producción gana en el estrato no canónico" sería trivial: el canon saca 0
            # ahí por definición. Aquí se construye una lista con EL MISMO perfil de calidad
            # que la de producción, película a película (±2 de VBS), pero elegida al azar.
            # Mismo VBS, cero personalización: la diferencia es personalización o no es nada.
            prod_vbs = dict((await db.execute(
                select(Movie.tmdb_id, Movie.vectorbox_score).where(Movie.tmdb_id.in_(prod))
            )).all())
            cubos: dict[int, list[int]] = {}
            for c, cv in pool_free:
                cubos.setdefault(int(cv // 2), []).append(c)
            # `usados` arranca con las vistas Y con la propia lista de producción: sin esto
            # el control puede elegir las MISMAS películas que controla. En cubos de VBS
            # poco poblados (el extremo canónico) colapsa sobre producción y la diferencia
            # sale exactamente 0.00 ±0.00 — así se detectó, un perfil canónico puro dio ese
            # cero imposible en 20 splits seguidos.
            emparejado, usados = [], set(watched) | set(prod)
            for t in prod:
                v = prod_vbs.get(t)
                if v is None:
                    continue
                cand = [c for c in cubos.get(int(v // 2), []) if c not in usados]
                if cand:
                    pick = rnd.choice(cand)
                    emparejado.append(pick)
                    usados.add(pick)

            # LONGITUDES IGUALADAS. Producción tiene un techo estructural: 7 anchors x
            # PER_ANCHOR_LIMIT=20 => ~140 candidatos como mucho, así que con K=500 devuelve 81
            # mientras las anclas rellenan 500. Comparar así premia a quien más huecos tiene:
            # el azar sacaba 4.50 con 500 huecos frente a 2.38 de producción con 81, o sea 3x
            # PEOR por hueco, y el veredicto salía al revés. Todas al tamaño real de producción.
            n = len(prod)
            canon = canon[:n]
            azar = azar_full[:n]
            emparejado = emparejado[:n]

            return {
                "held": len(held_tmdb),
                "reachable": len(reachable),
                "returned": len(prod),
                "PRODUCCIÓN": len(set(prod) & reachable),
                "cruda (pre-gate)": len(set(prod_raw) & reachable),
                "ANCLA canon": len(set(canon) & reachable),
                "ANCLA azar": len(set(azar) & reachable),
                "CONTROL VBS-emparejado": len(set(emparejado) & reachable),
                # Escepticismo: si el emparejado no reproduce el perfil de calidad de
                # producción, no controla nada. Se imprime para poder desconfiar.
                "_vbs_prod": float(np.mean([prod_vbs[t] for t in prod if prod_vbs.get(t) is not None]) or 0),
                "_vbs_ctrl": float(np.mean([v for t, v in pool_free if t in set(emparejado)]) or 0),
                "_n_ctrl": len(emparejado),
                "_no_canon": len([t for t in reachable if (await db.scalar(
                    select(Movie.vectorbox_score).where(Movie.tmdb_id == t))) or 0 < 85]),
            }
        finally:
            await db.execute(delete(UserRating).where(UserRating.user_id == sid))
            await db.execute(delete(User).where(User.id == sid))
            await db.commit()


async def main() -> None:
    orphans = await _cleanup_shadows()
    if orphans:
        print(f"(limpiadas {orphans} sombras huérfanas)")
    qd = QdrantService()
    print(f"K={K} · {SPLITS} splits · queridas = rating ≥ {HIGH_RATING}\n")

    for user_id in USERS:
        acc: dict[str, list[int]] = {}
        meta: dict[str, list[int]] = {}
        for sp in range(SPLITS):
            r = await _run_split(user_id, sp, qd)
            if not r:
                print(f"user {user_id}: saltado (pocas películas queridas)")
                break
            for k in ("PRODUCCIÓN", "cruda (pre-gate)", "CONTROL VBS-emparejado", "ANCLA canon", "ANCLA azar"):
                acc.setdefault(k, []).append(r[k])
            for k in ("held", "reachable", "returned", "_vbs_prod", "_vbs_ctrl", "_n_ctrl"):
                meta.setdefault(k, []).append(r[k])
        if not acc:
            continue
        print(f"===== USER {user_id} =====")
        print(f"  ocultas por split: {np.mean(meta['held']):.0f} "
              f"(alcanzables: {np.mean(meta['reachable']):.0f}) · producción devolvió "
              f"{np.mean(meta['returned']):.0f} de {K} pedidas")
        for name in ("PRODUCCIÓN", "cruda (pre-gate)", "CONTROL VBS-emparejado", "ANCLA canon", "ANCLA azar"):
            m, h = ci95(acc[name])
            print(f"  {name:14s} {m:6.2f} ±{h:.2f} aciertos de {np.mean(meta['reachable']):.0f}")
        print(f"  [control] VBS medio producción={np.mean(meta['_vbs_prod']):.1f} vs "
              f"emparejado={np.mean(meta['_vbs_ctrl']):.1f} · el emparejado tiene "
              f"{np.mean(meta['_n_ctrl']):.0f} de {K} películas")
        d, dh = ci95(np.array(acc["PRODUCCIÓN"]) - np.array(acc["CONTROL VBS-emparejado"]))
        print(f"  --> pareado PRODUCCIÓN − VBS-emparejado: {d:+.2f} ±{dh:.2f} "
              f"{'(personaliza)' if d - dh > 0 else '(NO se distingue de azar de igual calidad)'}")
        d, dh = ci95(np.array(acc["PRODUCCIÓN"]) - np.array(acc["ANCLA canon"]))
        print(f"  --> pareado PRODUCCIÓN − canon: {d:+.2f} ±{dh:.2f} "
              f"{'(producción gana)' if d - dh > 0 else '(NO bate al ancla)'}")
        d, dh = ci95(np.array(acc["PRODUCCIÓN"]) - np.array(acc["ANCLA azar"]))
        print(f"  --> pareado PRODUCCIÓN − azar : {d:+.2f} ±{dh:.2f} "
              f"{'(producción gana)' if d - dh > 0 else '(NO bate al azar)'}\n")

    left = await _cleanup_shadows()
    print(f"limpieza final: {left} sombras restantes")


if __name__ == "__main__":
    asyncio.run(main())
