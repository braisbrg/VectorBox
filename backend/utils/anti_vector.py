"""Anti-vector: weighted L2-normalized mean of a user's negative signals.

Used by both `RecommendationEngine` and `RecommendationService` to pull
candidate vectors AWAY from films a user has rejected or rated poorly,
decayed by age so old dislikes lose influence.

Lives in `utils/` rather than either service because the function is pure
(no instance state, no business policy) and both services need it. Putting
it in either service file would re-introduce the engine→service import
cycle the previous duplicate copies were created to avoid.

Note: this module is only the VECTOR computation. The downstream penalty
policy (drop vs. demote, thresholds, multipliers) is intentionally NOT
shared — `RecommendationEngine.get_because_you_watched_section` and
`RecommendationService._compute_vibe_signal_raw` apply different penalty
curves and that divergence is a design choice, not duplication.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional

import numpy as np
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.database import Movie, UserRating
from services.qdrant_service import QdrantService


HALF_LIFE_DAYS = 365
MIN_NEGATIVE_FILMS = 3       # below this the signal is too noisy — return None
MAX_NEGATIVE_FILMS = 50      # cap query cost; older entries drop off via decay
MIN_EFFECTIVE_WEIGHT = 0.05  # films whose decayed weight falls below this are skipped


# Qué fracción de una lista se considera "lo más parecido a lo que no te gusta".
# Es una FRACCIÓN y no un coseno porque un coseno absoluto no significa nada en
# este espacio: medido el 2026-09-01 sobre los heads reales de 10 usuarios, el
# umbral fijo de 0.65 tocaba entre 0 y 22 de 30 según el usuario — a u210 le
# demotaba 22, que al ser `×0.5` uniforme sobre 22 de 30 no penalizaba nada: sólo
# SUBÍA a los 8 que escapaban, elegidos por dónde caía la raya en una
# distribución sin hueco (los 8 entre 0.552 y 0.644, los 22 justo por encima).
ANTI_SIMILARITY_DEMOTE_FRACTION = 0.10


def most_anti_similar(cos_by_id: dict, fraction: float = ANTI_SIMILARITY_DEMOTE_FRACTION) -> set:
    """Los ids del decil más cercano al anti-vector DENTRO de esta lista.

    Relativo a la lista que se juzga, así que se auto-calibra por usuario y por
    fila: "penalizado" vuelve a ser la excepción (2-3 de 30 en los diez usuarios
    medidos) en vez de oscilar del 0% al 73%.

    Nunca devuelve la lista entera: con `fraction` pequeña y listas cortas, `k`
    baja a 1. Los empates entran los dos — pasa poco con flotantes y es preferible
    a partir un empate por orden de diccionario.

    ⚠ **LA TASA ES CONSTANTE POR CONSTRUCCIÓN, y eso es el precio del arreglo.**
    Esto penaliza SIEMPRE el 10%, tenga el usuario aversiones fuertes o ninguna:
    no sabe decir «en esta lista no hay nada que se parezca a lo que odias». Se
    cambió una varianza sin control (0%-73% según a quién) por un sesgo conocido,
    que es mejor trato pero sigue siendo un trato.

    El caso que lo enseña, medido el 2026-09-01: el head entero de u280 vive entre
    coseno 0.300 y 0.437 —el umbral viejo pedía >0.65, o sea que NADA de esa lista
    se parecía a sus negativas, y sobre el catálogo completo sólo 3 películas de
    21.405 le pasaban de 0.65— y aun así aquí se le penalizan 2 de 20. Antes: 0.

    Camino a v2 si molesta, sin volver al umbral fijo: comparar contra la
    distribución de coseno de ESE usuario sobre el catálogo entero, en vez de
    contra la de la lista. Es un percentil igual —así que sigue sin depender de
    un absoluto— pero con un referente que sí puede quedarse a cero cuando la
    fila no trae nada destacable. Requiere cachear un percentil por usuario.
    """
    if not cos_by_id:
        return set()
    k = max(1, round(fraction * len(cos_by_id)))
    corte = sorted(cos_by_id.values(), reverse=True)[k - 1]
    return {i for i, c in cos_by_id.items() if c >= corte}


def _rating_weight(is_rejected: bool, rating: Optional[float]) -> Optional[float]:
    """Map a negative rating to its raw (pre-decay) anti-vector weight.

    Most users never rate below 3 stars, so the legacy <=2 floor produced
    None for almost everyone. Mild 3-star negatives carry a small weight
    so the vector stays alive for typical users.
    """
    if is_rejected:
        return 2.0
    if rating is None:
        return None
    if rating <= 2.0:
        return 1.5
    if rating <= 2.5:
        return 1.0
    if rating <= 3.0:
        return 0.4
    return None


async def compute_anti_vector(
    user_id: int,
    db: AsyncSession,
    qdrant: QdrantService,
) -> Optional[list[float]]:
    """Return the user's anti-vector, or None when there's too little signal.

    Pulls up to 50 negative entries (`is_rejected=True` OR `rating <= 3.0`),
    weights each by rating bucket × age-decay (365-day half-life), and
    returns the L2-normalized weighted mean of their stored Qdrant vectors.
    """
    rating_result = await db.execute(
        select(UserRating, Movie.tmdb_id)
        .join(Movie, UserRating.movie_id == Movie.id)
        .where(UserRating.user_id == user_id)
        .where(or_(UserRating.is_rejected.is_(True), UserRating.rating <= 3.0))
        # Sin ORDER BY, el LIMIT coge 50 filas ARBITRARIAS y luego el decay las
        # tira: medido el 2026-09-01, u210 tenía 615 negativas (194 de los ultimos
        # 3 años) y de las 50 que devolvía Postgres 49 caían bajo
        # MIN_EFFECTIVE_WEIGHT — el usuario con más datos se quedaba SIN
        # anti-vector. El decay debe descartar lo viejo, no lo que nadie eligió.
        .order_by(UserRating.watched_date.desc().nullslast())
        .limit(MAX_NEGATIVE_FILMS)
    )
    rows = rating_result.all()
    if len(rows) < MIN_NEGATIVE_FILMS:
        return None

    tmdb_ids = [tmdb_id for _, tmdb_id in rows if tmdb_id is not None]
    if len(tmdb_ids) < MIN_NEGATIVE_FILMS:
        return None

    vectors_map = await qdrant.get_vectors_batch(tmdb_ids)
    if len(vectors_map) < MIN_NEGATIVE_FILMS:
        return None

    now = datetime.now(timezone.utc)
    weighted_vectors: list[np.ndarray] = []
    weights: list[float] = []

    for ur, tmdb_id in rows:
        vec = vectors_map.get(tmdb_id)
        if vec is None:
            continue
        raw_w = _rating_weight(bool(ur.is_rejected), ur.rating)
        if raw_w is None:
            continue

        # `created_at` NO sirve de reserva: es la fecha de import, idéntica para
        # cientos de filas del mismo ZIP y "hoy" tras re-subir, así que mezclaba
        # dos marcos temporales y daba peso MÁXIMO a lo que no tiene fecha. Y
        # como nunca es NULL, la rama neutra de abajo era inalcanzable.
        ref_date = ur.watched_date
        if ref_date is not None:
            if ref_date.tzinfo is None:
                ref_date = ref_date.replace(tzinfo=timezone.utc)
            days_ago = max(0, (now - ref_date).days)
        else:
            days_ago = HALF_LIFE_DAYS  # undated rows assumed one half-life old
        w = raw_w * (0.5 ** (days_ago / HALF_LIFE_DAYS))

        if w < MIN_EFFECTIVE_WEIGHT:
            continue
        weighted_vectors.append(np.array(vec) * w)
        weights.append(w)

    if len(weighted_vectors) < MIN_NEGATIVE_FILMS:
        return None

    def _compute_mean() -> list[float]:
        total = float(sum(weights))
        mean = np.sum(np.stack(weighted_vectors), axis=0) / total
        norm = float(np.linalg.norm(mean))
        if norm > 0:
            mean = mean / norm
        return mean.tolist()

    return await asyncio.get_running_loop().run_in_executor(None, _compute_mean)
