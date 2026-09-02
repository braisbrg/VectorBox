"""
User management router
"""
import re
from statistics import median

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func, delete, or_
from sqlalchemy.exc import IntegrityError
import logging

from config import get_db, REDIS_URL
from dependencies import get_current_user, get_http_client, verify_user_ownership
from limiter import limiter
from models.database import User, UserRating, Movie, UserCluster
from models.schemas import UserResponse, TokenResponse, LinkLetterboxdRequest, UserPreferencesRequest

logger = logging.getLogger(__name__)
router = APIRouter()

# Letterboxd usernames are 2–15 chars, lowercase letters/numbers/underscore.
# Source: signup form rejects anything else. Validate strictly so we don't
# end up making requests for `..`, `foo/bar`, `?q=`, or other path-injection
# shapes — bounded to letterboxd.com so not SSRF, but still wrong-looking.
LETTERBOXD_USERNAME_RE = re.compile(r"^[a-z0-9_]{2,15}$")

# Trident thresholds — below this vote count a film counts as an obscure find.
# ponytail: fixed thresholds, not catalogue percentiles — a percentile costs a
# second query on every profile load. Revisit if the vote distribution shifts.
# 2.6% of catalogue rows have no IMDb count and the TMDB fallback is a different
# scale entirely (measured median imdb/tmdb ratio = 41), so it gets scaled.
GEM_IMDB_VOTES = 50_000
GEM_TMDB_VOTES = GEM_IMDB_VOTES // 40


def trident_legs(loved: list, loved_director_counts: dict) -> dict:
    """{vibe, auteur, gems} — a PARTITION of the films you loved, so the three
    legs sum to 1 and every film lands in exactly one bucket, cheapest signal
    first: a director you love repeatedly, else an obscure find, else theme.
    Same three legs /why shows for a single recommendation.

    `loved` is [(directors, imdb_votes, tmdb_votes), ...].

    The old version scaled the auteur share by 3 and clamped it at 1, which
    saturated at a third of the library — braisbg (34% repeat-director films)
    and a guest with 35 onboarding ratings both read auteur ≥ 0.5, and gems
    never left 0.1 for anyone. Re-measured across 9 libraries 2026-07-31:
    auteur now spans 0.09–0.59, vibe 0.31–0.87, gems 0.00–0.18.
    """
    repeat_directors = {d for d, c in loved_director_counts.items() if c >= 2}
    legs = {"vibe": 0, "auteur": 0, "gems": 0}
    for directors, imdb_votes, tmdb_votes in loved:
        if any(d in repeat_directors for d in directors):
            legs["auteur"] += 1
        elif imdb_votes < GEM_IMDB_VOTES if imdb_votes else (tmdb_votes or 0) < GEM_TMDB_VOTES:
            legs["gems"] += 1
        else:
            legs["vibe"] += 1
    return {k: round(v / max(len(loved), 1), 2) for k, v in legs.items()}


# M-1: Legacy POST /api/users removed. Use POST /api/auth/register instead.


@router.get("/me/profile")
async def get_my_profile(
    current_user: TokenResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Aggregates for the ACID /you profile hub (H2 split dossier).

    All values derive from persisted data — no LLM calls on this path:
      - stats: rated count, cluster count, avg ★, day streak, this-month count
      - signature: the Redis-cached LLM profile summary (null if not generated)
      - tier: presentation heuristic over library size
      - trident: {vibe, auteur, gems} — auteur = share of 4★+ films whose
        director repeats, gems = share of low-vote-count films, vibe = rest
      - activity: films per month, last 12 months (sparkline)
      - top_clusters + recently_rated strips
    """
    from datetime import datetime, timedelta, timezone

    user_id = current_user.user_id
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Core stats in one aggregate.
    stats_row = (
        await db.execute(
            select(
                func.count(),
                func.avg(UserRating.rating),
            ).where(UserRating.user_id == user_id, UserRating.rating.isnot(None))
        )
    ).first()
    films = int(stats_row[0] or 0)
    avg_rating = float(stats_row[1]) if stats_row[1] is not None else None

    clusters = (
        await db.execute(
            select(UserCluster)
            .where(UserCluster.user_id == user_id)
            .order_by(desc(UserCluster.movie_count))
        )
    ).scalars().all()

    # Watched dates → streak + monthly sparkline (walk in Python; a library is
    # a few thousand rows at most).
    dates = (
        await db.execute(
            select(UserRating.watched_date)
            .where(UserRating.user_id == user_id, UserRating.watched_date.isnot(None))
            .order_by(desc(UserRating.watched_date))
        )
    ).scalars().all()
    day_set = {d.date() for d in dates}
    today = datetime.now(timezone.utc).date()
    streak = 0
    cursor = today if today in day_set else today - timedelta(days=1)
    while cursor in day_set:
        streak += 1
        cursor -= timedelta(days=1)

    sparkline = []
    month_counts: dict = {}
    for d in dates:
        key = (d.year, d.month)
        month_counts[key] = month_counts.get(key, 0) + 1
    mcursor = today.replace(day=1)
    for _ in range(12):
        sparkline.append(month_counts.get((mcursor.year, mcursor.month), 0))
        mcursor = (mcursor - timedelta(days=1)).replace(day=1)
    sparkline.reverse()
    this_month = sparkline[-1] if sparkline else 0

    # Trident split from persisted signals (presentation heuristic, sums to 1).
    # Same pass also feeds the taste-card badge sources (top director/actor/genres).
    rated_movies = (
        await db.execute(
            select(UserRating.rating, UserRating.is_liked, Movie.directors, Movie.cast,
                   Movie.genres, Movie.imdb_vote_count, Movie.vote_count)
            .join(Movie, Movie.id == UserRating.movie_id)
            .where(UserRating.user_id == user_id, UserRating.rating.isnot(None))
        )
    ).all()
    director_ratings: dict = {}   # name → [ratings], for "highest rated director"
    actor_ratings: dict = {}      # name → [ratings]
    genre_counts: dict = {}       # genre → count among loved films
    loved: list = []              # (directors, imdb_votes, tmdb_votes) — what the trident partitions
    loved_director_counts: dict = {}
    for rating, is_liked, directors, cast, genres, imdb_votes, tmdb_votes in rated_movies:
        for d in directors or []:
            director_ratings.setdefault(d, []).append(rating or 0)
        for a in cast or []:
            actor_ratings.setdefault(a, []).append(rating or 0)
        if (rating or 0) >= 3.5:
            for g in genres or []:
                genre_counts[g] = genre_counts.get(g, 0) + 1
        if (rating or 0) >= 4.0 or is_liked:
            loved.append((directors or [], imdb_votes, tmdb_votes))
            for d in directors or []:
                loved_director_counts[d] = loved_director_counts.get(d, 0) + 1

    # Badge sources: the name whose films you rate highest, with Bayesian shrinkage
    # toward your global mean (same idea as VBS) so a 2-film co-star can't outrank a
    # genuine favourite. Without it, Anne Hathaway (Interstellar 5★ + Alice 4.5★, 2
    # films) beat Nolan (7 films @ 4.5) for braisbg — she's incidental to why those
    # films are loved. Actors need a higher floor (≥3) than directors (≥2): an actor
    # is far more incidental to a film's appeal than its director. Genres by frequency.
    _gmean = avg_rating if avg_rating is not None else 3.5
    def _top_shrunk(m: dict, min_films: int, prior: float = 4.0):
        best, best_score = None, -1.0
        for k, ratings in m.items():
            if len(ratings) < min_films:
                continue
            avg = sum(ratings) / len(ratings)
            adj = (len(ratings) * avg + prior * _gmean) / (len(ratings) + prior)
            if adj > best_score:
                best, best_score = k, adj
        return best
    top_director = _top_shrunk(director_ratings, 2)
    top_actor = _top_shrunk(actor_ratings, 3)
    top_genres = sorted(genre_counts, key=lambda g: genre_counts[g], reverse=True)[:3]

    if not loved:  # rates everything 3★ — partition what there is rather than nothing
        loved = [(d or [], iv, tv) for _r, _l, d, _c, _g, iv, tv in rated_movies]
    trident = trident_legs(loved, loved_director_counts)

    tier_n = 1 + (films >= 100) + (films >= 500) + (films >= 1000)
    tier = f"CINEPHILE · TIER {'I' * tier_n if tier_n <= 3 else 'IV'}"

    # "Recently rated" means recently WATCHED, not recently inserted. created_at
    # never moves on update, so an RSS re-sync that rates a film already in the
    # library (ZIP import, watchlist row) left it buried under the import batch:
    # The 47, watched 2026-07-29, sat behind rows created the same May minute.
    # watched_date first, created_at only for rows that have none (old ZIPs).
    recent = (
        await db.execute(
            select(UserRating, Movie)
            .join(Movie, Movie.id == UserRating.movie_id)
            .where(UserRating.user_id == user_id, UserRating.rating.isnot(None))
            .order_by(desc(UserRating.watched_date).nullslast(), desc(UserRating.created_at))
            .limit(6)
        )
    ).all()

    # Defining films for the taste card — your genuinely loved films (≥4★ or liked),
    # highest first, not merely the most recently rated. Posters need a valid path.
    defining = (
        await db.execute(
            select(Movie)
            .join(UserRating, Movie.id == UserRating.movie_id)
            .where(
                UserRating.user_id == user_id,
                Movie.poster_path.isnot(None),
                or_(UserRating.rating >= 4.0, UserRating.is_liked.is_(True)),
            )
            .order_by(desc(UserRating.rating), desc(UserRating.created_at))
            .limit(6)
        )
    ).scalars().all()

    # LLM profile summary — cached only, never generated inline.
    signature = None
    try:
        from services.profile_cache import get_cached_profile_summary
        signature = await get_cached_profile_summary(user_id, REDIS_URL)
    except Exception as e:
        logger.warning(f"Profile summary fetch failed for user {user_id}: {e}")

    return {
        "username": user.username,
        "letterboxd_username": user.letterboxd_username,
        "member_since": user.created_at.strftime("%b %Y").lower() if user.created_at else None,
        "tier": tier,
        "signature": signature,
        "stats": {
            "films": films,
            "clusters": len(clusters),
            "avg_rating": round(avg_rating, 1) if avg_rating is not None else None,
            "streak_days": streak,
            "this_month": this_month,
        },
        "trident": trident,
        "activity_sparkline": sparkline,
        "top_clusters": [
            {"id": c.cluster_id, "name": c.cluster_label, "films": c.movie_count}
            for c in clusters[:5]
        ],
        # Optional taste-card badge sources (real data; user toggles which to show).
        "top_director": top_director,
        "top_actor": top_actor,
        "top_genres": top_genres,
        # Taste-card "defining films" — your loved films (≥4★/liked), highest first.
        "defining_films": [
            {"tmdb_id": m.tmdb_id, "title": m.title, "year": m.year, "poster_url": m.poster_path}
            for m in defining
        ],
        "recently_rated": [
            {
                "tmdb_id": m.tmdb_id,
                "title": m.title,
                "year": m.year,
                "poster_url": m.poster_path,
                "rating": r.rating,
                "q": m.vectorbox_score,
            }
            for r, m in recent
        ],
    }


# Runtime bands — the shape of a filmography, not arbitrary: sub-90 is the short
# feature, 90–120 the default, 150+ the commitment.
RUNTIME_BANDS = ((90, "<90"), (120, "90–120"), (150, "120–150"), (10_000, "150+"))

# NOTA (2026-08-10): aquí vivía BURST_DAY_FILMS, un filtro que descartaba los días
# con más de 10 películas porque Letterboxd metía TRES fechas distintas en la misma
# columna y un backfill sellaba cientos de filas con un solo día (medido: 121
# películas el 2023-01-22 para un usuario, el 38% de su biblioteca; inventaba un
# hábito de ver cine los miércoles que desapareció al filtrarlo).
#
# Ya no hace falta: las tres fuentes de fecha falsa están cortadas en origen
# (data_processor ya no escribe las de ratings.csv/watched.csv, movies.py ya no
# sella datetime.now()), así que `watched_date` significa una sola cosa —
# entrada de diario real— y NULL significa "no se sabe". Una heurística que
# adivina lo que el dato ya dice sólo puede equivocarse: descartaba maratones
# reales de 11 películas.
#
# Las filas importadas ANTES de este cambio siguen sucias hasta que su ZIP se
# vuelva a subir; el upsert las limpia solo, porque ahora escribe NULL encima.


def stats_buckets(rows: list) -> dict:
    """The /stats payload, bucketed from raw rows.

    `rows` is [(rating, watched_date, watch_count, year, runtime, genres,
    original_language, directors, title, tmdb_id, imdb_rating, imdb_votes,
    countries, cast, mood_gravedad, mood_humanidad), ...] — one per watched film.

    Every facet is gated on the field it needs and reports its own denominator,
    because coverage is NOT uniform (measured over the real catalogue 2026-08-04:
    imdb_rating 98%, countries 99%, runtime 99% — but watched_date is 100% for
    Letterboxd imports and 0% for onboarding-only users). A facet that silently
    averages over whatever happens to be non-null reports a confident wrong number.

    Pure so it can be tested without a database, same as trident_legs above.
    """
    # 0.5★ buckets, always all 10 keys so the chart keeps a stable x-axis.
    histogram = {round(0.5 * i, 1): 0 for i in range(1, 11)}
    decades: dict = {}
    genres: dict = {}
    languages: dict = {}
    directors: dict = {}
    per_year: dict = {}
    countries: dict = {}
    actors: dict = {}
    bands = {label: 0 for _, label in RUNTIME_BANDS}
    weekday = [0] * 7
    decade_ratings: dict = {}   # decade → [ratings], for "which era you rate highest"
    total_minutes = 0   # counts rewatches — this is time in front of a screen
    runtime_sum = 0     # does NOT — this is how long your films are
    runtime_films = 0
    rated = 0
    dated = 0
    diary_dated = 0             # dates that survive the burst filter — the honest denominator
    votes: list = []            # imdb vote counts, for the obscurity median
    deltas: list = []           # (your ★×2 − crowd's /10, film) — only where both exist
    rewatched: list = []

    mood_cells = {q: 0 for q in ("moving", "dark", "comforting", "popcorn")}
    mood_grav: list = []
    mood_hum: list = []

    for (rating, watched_date, watch_count, year, runtime, movie_genres, lang,
         movie_directors, title, tmdb_id, imdb_rating, imdb_votes, movie_countries,
         movie_cast, grav, hum) in rows:
        plays = max(watch_count or 1, 1)
        film = {"tmdb_id": tmdb_id, "title": title, "year": year}
        if rating is not None:
            # Letterboxd half-stars land exactly on the buckets; clamp anything else.
            bucket = min(max(round(rating * 2) / 2, 0.5), 5.0)
            histogram[bucket] += 1
            rated += 1
        if year:
            decades[(year // 10) * 10] = decades.get((year // 10) * 10, 0) + 1
            if rating is not None:
                decade_ratings.setdefault((year // 10) * 10, []).append(rating)
        if runtime:
            total_minutes += runtime * plays
            runtime_sum += runtime
            runtime_films += 1
            for ceiling, label in RUNTIME_BANDS:
                if runtime < ceiling:
                    bands[label] += 1
                    break
        for g in movie_genres or []:
            genres[g] = genres.get(g, 0) + 1
        if lang:
            languages[lang] = languages.get(lang, 0) + 1
        for c in movie_countries or []:
            countries[c] = countries.get(c, 0) + 1
        for d in movie_directors or []:
            directors[d] = directors.get(d, 0) + 1
        for a in movie_cast or []:
            actors[a] = actors.get(a, 0) + 1
        if watched_date:
            # Toda fecha que llega aquí es ya una fecha real de visionado, así que
            # `dated` y `diary_dated` son lo mismo. Se mantienen los dos campos
            # para no romper el contrato del frontend, que enseña el denominador.
            dated += 1
            diary_dated += 1
            per_year[watched_date.year] = per_year.get(watched_date.year, 0) + 1
            weekday[watched_date.weekday()] += 1
        if imdb_votes:
            votes.append(imdb_votes)
        # ★ is a 0.5–5 scale, IMDb is 1–10 — double before comparing, or every
        # user looks like a harsh critic.
        if rating is not None and imdb_rating is not None:
            deltas.append((round(rating * 2 - imdb_rating, 2), film))
        if plays > 1:
            rewatched.append((plays, film))
        # Mood: el centro de masa va sobre TODAS las que tienen ejes; los cuatro
        # cuadrantes sólo cuentan las que caen fuera de la banda muerta 40-60, así
        # que sus cuatro números NO suman `films` — el frontend enseña su propio
        # denominador en vez de dejar que se lean como porcentajes del total.
        if grav is not None and hum is not None:
            mood_grav.append(grav)
            mood_hum.append(hum)
            if grav >= 60 and hum >= 60:
                mood_cells["moving"] += 1
            elif grav >= 60 and hum <= 40:
                mood_cells["dark"] += 1
            elif grav <= 40 and hum >= 60:
                mood_cells["comforting"] += 1
            elif grav <= 40 and hum <= 40:
                mood_cells["popcorn"] += 1

    def _top(m: dict, n: int):
        return [{"name": k, "films": v} for k, v in sorted(m.items(), key=lambda kv: -kv[1])[:n]]

    return {
        "films": len(rows),
        "rated_films": rated,
        # The diary is partial: ratings.csv rows carry no date, so the per-year
        # chart covers `dated_films` of `films`. The UI must say so rather than
        # let a short bar read as a quiet year.
        "dated_films": dated,
        # ...and of those, the ones on a plausible viewing day. This — never
        # dated_films — is what the per-year and weekday charts actually plot.
        "diary_films": diary_dated,
        "runtime": {
            "total_hours": round(total_minutes / 60),
            "avg_minutes": round(runtime_sum / runtime_films) if runtime_films else 0,
        },
        "rating_histogram": [{"stars": s, "films": histogram[s]} for s in sorted(histogram)],
        "decades": [{"decade": d, "films": decades[d]} for d in sorted(decades)],
        "per_year": [{"year": y, "films": per_year[y]} for y in sorted(per_year)],
        "genres": _top(genres, 10),
        "languages": _top(languages, 8),
        "countries": _top(countries, 8),
        "directors": _top(directors, 10),
        # Movie.cast holds the top-billed 3, so this is "leads you keep seeing",
        # not full filmographies.
        "actors": _top(actors, 10),
        "runtime_bands": [{"name": label, "films": bands[label]} for _, label in RUNTIME_BANDS],
        # Only weekdays if there is a diary to read them from — 0 for onboarding users.
        "weekday": [{"day": i, "films": n} for i, n in enumerate(weekday)] if diary_dated else [],
        # An era you watch a lot is not an era you like — this is the second one.
        # 3-film floor: a single 5★ would otherwise crown the 1920s.
        "decade_ratings": [
            {"decade": d, "avg": round(sum(rs) / len(rs), 2), "films": len(rs)}
            for d, rs in sorted(decade_ratings.items()) if len(rs) >= 3
        ],
        "obscurity": {
            "median_votes": int(median(votes)) if votes else None,
            "obscure_share": round(sum(1 for v in votes if v < GEM_IMDB_VOTES) / len(votes), 2) if votes else None,
            "films": len(votes),
        },
        # Where you and the crowd disagree. `films` is the denominator: films
        # carrying BOTH your rating and an IMDb one, never the whole library.
        "vs_crowd": {
            "delta": round(sum(d for d, _ in deltas) / len(deltas), 2) if deltas else None,
            "films": len(deltas),
            "above": [{**f, "delta": d} for d, f in sorted(deltas, key=lambda x: -x[0])[:5]],
            "below": [{**f, "delta": d} for d, f in sorted(deltas, key=lambda x: x[0])[:5]],
        },
        # Dónde cae tu gusto en los dos ejes. `films` es su propio denominador:
        # las películas sin mood calculado no cuentan, y los cuatro cuadrantes
        # dejan fuera la banda muerta del centro.
        "mood": {
            "films": len(mood_grav),
            "gravedad": round(sum(mood_grav) / len(mood_grav), 1) if mood_grav else None,
            "humanidad": round(sum(mood_hum) / len(mood_hum), 1) if mood_hum else None,
            "quadrants": [{"name": k, "films": v} for k, v in mood_cells.items()],
        },
        "rewatches": {
            "films": len(rewatched),
            "extra_plays": sum(p - 1 for p, _ in rewatched),
            "top": [{**f, "plays": p} for p, f in sorted(rewatched, key=lambda x: -x[0])[:5]],
        },
    }


@router.get("/me/stats")
async def get_my_stats(
    current_user: TokenResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Distributions for /stats — what /me/profile does NOT already cover.

    /me/profile owns the headline numbers (count, avg ★, streak, 12-month
    sparkline, clusters, top director/actor). This owns the shapes: star
    histogram, release decades, genres, languages, runtime hours, films per
    calendar year watched.

    Scope is `is_watched` — watchlist-only rows are not films you saw.
    ponytail: one select + Python bucketing, same pattern as /me/profile above.
    A library is a few thousand rows; a GROUP BY per facet would be 6 round trips.
    """
    rows = (
        await db.execute(
            select(
                UserRating.rating, UserRating.watched_date, UserRating.watch_count,
                Movie.year, Movie.runtime, Movie.genres, Movie.original_language,
                Movie.directors, Movie.title, Movie.tmdb_id, Movie.imdb_rating,
                Movie.imdb_vote_count, Movie.omdb_countries, Movie.cast,
                Movie.mood_gravedad, Movie.mood_humanidad,
            )
            .join(Movie, Movie.id == UserRating.movie_id)
            .where(UserRating.user_id == current_user.user_id, UserRating.is_watched.is_(True))
        )
    ).all()
    return stats_buckets(rows)


@router.delete("/me/ratings/{tmdb_id}")
@limiter.limit("30/minute")
async def delete_my_rating(
    # slowapi needs the starlette Request named `request` (F0 lesson).
    request: Request,
    tmdb_id: int,
    current_user: TokenResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Un-rate a film — removes the signed-in user's rating for it (profile
    'recently rated' → ✕). Idempotent: a no-op if there was no rating."""
    movie = (await db.execute(select(Movie).where(Movie.tmdb_id == tmdb_id))).scalar_one_or_none()
    if not movie:
        raise HTTPException(status_code=404, detail="Movie not found")
    await db.execute(
        delete(UserRating).where(
            UserRating.user_id == current_user.user_id,
            UserRating.movie_id == movie.id,
        )
    )
    await db.commit()
    return {"status": "ok", "tmdb_id": tmdb_id}


@router.get("", response_model=list[UserResponse])
async def list_users(
    db: AsyncSession = Depends(get_db),
    current_user: TokenResponse = Depends(get_current_user),
):
    """
    Return ONLY the calling user's profile.

    Historically this endpoint listed every user on the platform with their
    has_data flag. Under Clerk auth there is no legitimate reason for the
    frontend to know about other users — the legacy "select session user"
    flow died with the multi-user-on-one-machine cookie model. The remaining
    frontend code path (upload-zone activeUserProfile lookup) only ever
    looks up its own ID, so we return a single-element list for shape
    compatibility.
    """
    from sqlalchemy import func
    from models.database import UserRating

    user_result = await db.execute(
        select(User).where(User.id == current_user.user_id)
    )
    user = user_result.scalar_one_or_none()
    if not user:
        return []

    rating_count = await db.scalar(
        select(func.count(UserRating.id)).where(UserRating.user_id == user.id)
    )
    return [{
        "id": user.id,
        "username": user.username,
        "country_code": user.country_code,
        "created_at": user.created_at,
        "has_data": (rating_count or 0) > 0,
        "letterboxd_username": user.letterboxd_username,
        "include_shorts": user.include_shorts,
    }]


@router.patch("/me/preferences", response_model=UserResponse)
@limiter.limit("30/minute")
async def update_preferences(
    body: UserPreferencesRequest,
    request: Request,
    current_user: TokenResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Ajustes de descubrimiento del propio usuario. `user_id` sale del token,
    nunca del cuerpo.

    Cambiar la preferencia invalida el feed cacheado: las filas se guardan por
    usuario (`section:{v}:{user_id}:*`) con TTL de 1-24 h, así que sin esto el
    toggle no se notaría hasta que expirase la fila más lenta.
    """
    user = await db.scalar(select(User).where(User.id == current_user.user_id))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if body.include_shorts is not None and body.include_shorts != user.include_shorts:
        user.include_shorts = body.include_shorts
        await db.commit()
        from services.cache_service import invalidate_user_cache
        await invalidate_user_cache(user.id)

    rating_count = await db.scalar(
        select(func.count(UserRating.id)).where(UserRating.user_id == user.id)
    )
    return {
        "id": user.id,
        "username": user.username,
        "country_code": user.country_code,
        "created_at": user.created_at,
        "has_data": (rating_count or 0) > 0,
        "letterboxd_username": user.letterboxd_username,
        "include_shorts": user.include_shorts,
    }


@router.patch("/{user_id}/link-letterboxd")
@limiter.limit("10/minute")
async def link_letterboxd(
    user_id: int,
    body: LinkLetterboxdRequest,
    request: Request,
    current_user: TokenResponse = Depends(verify_user_ownership),
    db: AsyncSession = Depends(get_db),
):
    """
    Link a Letterboxd profile to a VectorBox user.
    L-3: Username moved from query param to request body for privacy.
    """
    letterboxd_username = (body.letterboxd_username or "").strip().lower()

    # Strict format validation BEFORE we even touch the network. Letterboxd
    # usernames are [a-z0-9_]{2,15}; anything else is either invalid or a
    # path-injection attempt (e.g. `foo/admin`, `..`, `bar?x=1`).
    if not LETTERBOXD_USERNAME_RE.match(letterboxd_username):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Letterboxd username — must be 2–15 lowercase letters, digits, or underscores.",
        )

    # Find user
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Liveness check vía curl_cffi, no httpx: Letterboxd está detrás de Cloudflare
    # y responde **403 a httpx para CUALQUIER perfil**, exista o no (medido
    # 2026-08-11: 403 tanto para `braisbg` como para un handle inventado). El
    # check anterior leía "no es 200" como "no existe", así que devolvía 400 a
    # todo el mundo — re-vincular llevaba roto desde siempre y no se notó porque
    # ningún botón llegaba a este endpoint.
    #
    # `_fetch_with_curl_cffi` sí distingue: 141 KB para el perfil real, None para
    # el inventado. Es el mismo cliente con el que ya se scrapea la watchlist.
    try:
        from services.scraper_service import ScraperService
        html = await ScraperService()._fetch_with_curl_cffi(
            f"https://letterboxd.com/{letterboxd_username}/",
            referer="https://letterboxd.com/",
        )
        if not html:
            # None cubre "no existe" y "no se pudo llegar", y no se pueden separar.
            # Se rechaza igual: vincular un handle con una errata deja al usuario
            # con una cuenta que sincroniza cero y sin ninguna pista de por qué,
            # que es peor que un error claro y reintentable. El mensaje no afirma
            # más de lo que sabemos.
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(f"Could not reach the Letterboxd profile '{letterboxd_username}'. "
                        f"Check the spelling, or try again in a minute."),
            )
    except HTTPException:
        raise
    except Exception as e:
        # Fallo del propio scraper (import, binario, etc.) — no es evidencia de
        # nada sobre el perfil. La regex de formato es la puerta de seguridad.
        logger.warning(f"Could not validate Letterboxd profile: {e}")

    user.letterboxd_username = letterboxd_username
    await db.commit()

    logger.info(f"User {user.username} linked Letterboxd profile: {letterboxd_username}")

    return {
        "message": "Letterboxd profile linked successfully",
        "user_id": user.id,
        "username": user.username,
        "letterboxd_username": letterboxd_username,
    }


@router.get("/{username}/activity")
async def get_user_activity(
    username: str,
    db: AsyncSession = Depends(get_db),
    current_user: TokenResponse = Depends(get_current_user)
):
    """
    Get user's last watched and last rated movies.
    H-1: Only the profile owner can access their activity.
    """
    from models.database import UserRating, Movie
    
    # H-1: Ownership check — users can only view their own activity
    if username != current_user.username:
        raise HTTPException(status_code=403, detail="Access denied: cannot view another user's activity")
    
    # Get User ID
    stmt = select(User).where(User.username == username)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    # Last Watched
    watched_stmt = select(Movie).join(UserRating).where(
        UserRating.user_id == user.id,
        UserRating.is_watched.is_(True)
    # Sin `nulls_last` esto devuelve una peli SIN fecha como "la ultima que viste":
    # 1.735 de 4.119 filas no la tienen (medido 2026-08-18).
    ).order_by(UserRating.watched_date.desc().nulls_last()).limit(1)
    
    watched_result = await db.execute(watched_stmt)
    last_watched = watched_result.scalar_one_or_none()
    
    # Last Rated (Explicit rating > 0)
    rated_stmt = select(Movie).join(UserRating).where(
        UserRating.user_id == user.id,
        UserRating.rating.isnot(None),
        UserRating.rating > 0
    ).order_by(UserRating.watched_date.desc().nulls_last()).limit(1)
    
    rated_result = await db.execute(rated_stmt)
    last_rated = rated_result.scalar_one_or_none()
    
    return {
        "last_watched": {
            "title": last_watched.title,
            "year": last_watched.year,
            "poster_path": last_watched.poster_path
        } if last_watched else None,
        "last_rated": {
            "title": last_rated.title,
            "year": last_rated.year,
            "poster_path": last_rated.poster_path
        } if last_rated else None
    }
