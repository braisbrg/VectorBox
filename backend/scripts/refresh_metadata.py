import asyncio
import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, date, timezone

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, or_, and_, update, text as _sa_text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import object_session as _sync_object_session
from sqlalchemy.ext.asyncio import async_object_session

from config import AsyncSessionLocal
from models.database import Movie
from services.tmdb_client import TMDBClient
from services.omdb_client import OMDbClient, parse_oscar_wins, split_omdb_csv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

logging.getLogger("httpx").setLevel(logging.WARNING)

REFRESH_STRATEGIES = [
    ("recent",  365,       7,  "< 1 year old → refresh weekly"),
    ("mid",     365 * 5,   30, "1-5 years → refresh monthly"),
    ("classic", None,      90, "5+ years → refresh quarterly"),
]


# parse_oscar_wins + split_omdb_csv are defined in services/omdb_client.py
# (shared with services/movie_factory.py for new-film ingest).


async def get_movies_to_refresh(db, strategy: str, limit: int, force: bool = False) -> list:
    """Pick movies due for refresh. `force=True` skips the age/threshold filters
    so the script can be used as a recovery tool to re-fetch every row in a
    cohort (useful when a previous refresh persisted only a subset of fields,
    e.g. the imdb_rating gap fixed in 2026-05-15)."""
    now = datetime.utcnow()

    if strategy == "recent":
        cutoff_year = now.year - 1
        threshold = now - timedelta(days=7)
        query = select(Movie).where(Movie.year >= cutoff_year)
        if not force:
            query = query.where(or_(
                Movie.last_metadata_refresh.is_(None),
                Movie.last_metadata_refresh < threshold,
            ))
    elif strategy == "mid":
        year_min = now.year - 5
        year_max = now.year - 1
        threshold = now - timedelta(days=30)
        query = select(Movie).where(Movie.year >= year_min, Movie.year < year_max)
        if not force:
            query = query.where(or_(
                Movie.last_metadata_refresh.is_(None),
                Movie.last_metadata_refresh < threshold,
            ))
    else:  # classic
        cutoff_year = now.year - 5
        threshold = now - timedelta(days=90)
        query = select(Movie).where(Movie.year < cutoff_year)
        if not force:
            query = query.where(or_(
                Movie.last_metadata_refresh.is_(None),
                Movie.last_metadata_refresh < threshold,
            ))

    # `LIMIT` sin `ORDER BY` no es una cola, es una muestra arbitraria del heap: con 179
    # elegibles y limit=100 la misma centena puede volver run tras run y las otras 79 no
    # refrescarse NUNCA. Así llevaban 38 filas de `movie_availability` en ES ancladas en
    # marzo-mayo (5 meses) mientras el resto del país estaba al día (medido 2026-08-19).
    # `nulls_first` porque en ASC Postgres pone los NULL al FINAL, y NULL aquí significa
    # "no se ha refrescado jamás" — los 422 más urgentes irían los últimos. Es el espejo
    # exacto de la regla de `nullslast()` del CLAUDE.md.
    query = query.order_by(Movie.last_metadata_refresh.asc().nulls_first())
    result = await db.execute(query.limit(limit))
    return result.scalars().all()


async def _guardar_disponibilidad(movie: Movie, tmdb_data: dict) -> None:
    """Persiste en `movie_availability` los proveedores que ya vienen en el detalle.

    Sólo `flatrate` (suscripción): que una película se pueda ALQUILAR no es tenerla, y
    mezclarlo haría que el filtro por proveedor del rail devolviese títulos que hay que
    pagar aparte. Escribe todos los países que TMDB traiga, no sólo ES — el dato ya está
    y `country_code` es parte de la clave, así que no cuesta nada guardarlos.
    """
    from sqlalchemy.dialects.postgresql import insert as _pg_insert
    from models.database import MovieAvailability
    from services.provider_service import es_ruido

    datos = (tmdb_data or {}).get("providers_data")
    if not isinstance(datos, dict):
        return
    # Un `{}` NO es una respuesta mala, es "no está en ningún sitio, en ningún país":
    # si la llamada a TMDB hubiera fallado, `refresh_movie` ya habría salido con None
    # antes de llegar aquí. Tratarlo como fallo dejaba tres películas (Tallulah, A Free
    # Man, Emotional Architecture 1959) afirmando Netflix/Filmin/Arte para siempre.

    ahora = datetime.now(timezone.utc)
    sesion = async_object_session(movie)
    if sesion is None:
        return
    # Este bucle sólo sabe AÑADIR y ACTUALIZAR: recorre los países que TMDB devuelve.
    # Cuando una película DEJA de estar en un país, TMDB deja de mandar ese bloque, así
    # que la fila vieja no se toca nunca y sigue afirmando el proveedor para siempre.
    # Medido 2026-08-19: 49 filas de ES afirmando disponibilidad con hasta 5 meses, y
    # **47 de ellas visitadas por este mismo refresco después de esa fecha** (A Serbian
    # Film se refrescó el 13-08 y estrenó fila de otro país ese día, con la de ES clavada
    # en el 21-04). Vaciamos aquí las que ya no vienen: `datos` no vacío significa que la
    # respuesta de TMDB es buena, así que un país ausente es un "ya no está", no un fallo.
    paises = [k for k in datos if isinstance(k, str) and len(k) == 2]
    limpiar = (update(MovieAvailability)
               .where(MovieAvailability.movie_id == movie.id,
                      MovieAvailability.providers != _sa_text("'[]'::jsonb"))
               .values(providers=[], last_updated=ahora))
    if paises:
        limpiar = limpiar.where(MovieAvailability.country_code.not_in(paises))
    await sesion.execute(limpiar)
    for pais, bloque in datos.items():
        if not isinstance(pais, str) or len(pais) != 2:
            continue
        flatrate = (bloque or {}).get("flatrate") or []
        # `es_ruido` quita "X with Ads" (el mismo servicio repetido: "Netflix Standard
        # with Ads" junto a "Netflix") y "X Amazon Channel" (suscripción de pago aparte
        # dentro de Prime, que con Prime a secas no puedes ver). Eran 19 de los 55
        # proveedores distintos de ES. Se filtra al ESCRIBIR para que ninguna superficie
        # tenga que acordarse. Ver services/provider_service.es_ruido.
        provs = [{"provider_id": p.get("provider_id"), "provider_name": p.get("provider_name")}
                 for p in flatrate
                 if p.get("provider_id") and not es_ruido(p.get("provider_name"))]
        stmt = _pg_insert(MovieAvailability).values(
            movie_id=movie.id, country_code=pais, providers=provs, last_updated=ahora,
        ).on_conflict_do_update(
            index_elements=["movie_id", "country_code"],
            set_={"providers": provs, "last_updated": ahora},
        )
        await sesion.execute(stmt)


async def refresh_movie(movie: Movie, tmdb: TMDBClient, omdb: OMDbClient) -> bool:
    try:
        tmdb_data = await tmdb.get_movie_details(movie.tmdb_id, force_refresh=True)
        if not tmdb_data:
            return False

        # TMDB fields. Preserve existing value if the new payload is missing
        # the key, otherwise overwrite — fixing a previously empty column
        # is exactly what this script is meant to do.
        movie.vote_count = tmdb_data.get("vote_count", movie.vote_count)
        movie.vote_average = tmdb_data.get("vote_average", movie.vote_average)
        movie.popularity = tmdb_data.get("popularity", movie.popularity)
        if tmdb_data.get("poster_path") is not None:
            movie.poster_path = tmdb_data["poster_path"]
        genres = tmdb_data.get("genres")
        if genres:
            movie.genres = [g["name"] for g in genres]
        movie.runtime = tmdb_data.get("runtime", movie.runtime)
        if tmdb_data.get("overview"):
            movie.overview = tmdb_data["overview"]
        if tmdb_data.get("original_language"):
            movie.original_language = tmdb_data["original_language"]
        # keywords_flat, directors, cast, title_es, overview_es are computed by
        # TMDBClient.get_movie_details from append_to_response — see tmdb_client.py
        if tmdb_data.get("keywords_flat"):
            movie.keywords = tmdb_data["keywords_flat"]
        if tmdb_data.get("directors"):
            movie.directors = tmdb_data["directors"]
        if tmdb_data.get("cast"):
            movie.cast = tmdb_data["cast"]
        if tmdb_data.get("title_es"):
            movie.title_es = tmdb_data["title_es"]
        if tmdb_data.get("overview_es"):
            movie.overview_es = tmdb_data["overview_es"]
        # imdb_id can change for very rare titles or be filled in late by TMDB.
        # Normalize empty string → None to avoid UNIQUE-constraint collisions.
        if not movie.imdb_id:
            new_imdb = (tmdb_data.get("imdb_id") or "").strip() or None
            if new_imdb:
                movie.imdb_id = new_imdb
        # Extended TMDB metadata (migration o3p4q5r6s7t8)
        if tmdb_data.get("tagline"):
            movie.tagline = tmdb_data["tagline"]
        if tmdb_data.get("backdrop_path"):
            movie.backdrop_path = tmdb_data["backdrop_path"]
        if tmdb_data.get("adult") is not None:
            movie.is_adult = bool(tmdb_data["adult"])
        collection = tmdb_data.get("belongs_to_collection")
        if collection and isinstance(collection, dict):
            movie.collection_id = collection.get("id")
            movie.collection_name = collection.get("name")

        if movie.imdb_id:
            omdb_data = await omdb.fetch_movie_data(movie.imdb_id)
            if omdb_data:
                if omdb_data.imdbVotes:
                    raw = omdb_data.imdbVotes.replace(",", "").strip()
                    if raw.isdigit():
                        movie.imdb_vote_count = int(raw)
                if omdb_data.imdbRating and omdb_data.imdbRating != "N/A":
                    try:
                        movie.imdb_rating = float(omdb_data.imdbRating)
                    except ValueError:
                        pass
                if omdb_data.Metascore and omdb_data.Metascore != "N/A":
                    try:
                        movie.metacritic_rating = int(omdb_data.Metascore)
                    except ValueError:
                        pass
                # Extended OMDb metadata (migration o3p4q5r6s7t8)
                if omdb_data.Rated and omdb_data.Rated != "N/A":
                    movie.mpaa_rating = omdb_data.Rated
                if omdb_data.Awards and omdb_data.Awards != "N/A":
                    movie.awards_text = omdb_data.Awards
                    movie.oscar_wins = parse_oscar_wins(omdb_data.Awards)
                countries = split_omdb_csv(omdb_data.Country)
                if countries:
                    movie.omdb_countries = countries
                languages = split_omdb_csv(omdb_data.Language)
                if languages:
                    movie.omdb_languages = languages
            effective_votes = max(movie.vote_count or 0, movie.imdb_vote_count or 0)
            if effective_votes >= 10:
                vb = omdb.calculate_vectorbox_score(
                    omdb_data,
                    movie.vote_average,
                    tmdb_vote_count=movie.vote_count,
                    imdb_vote_count=movie.imdb_vote_count,
                )
                if vb.score is not None:
                    movie.vectorbox_score = vb.score

        # Fechas por país DERIVADAS del bloque `release_dates` que ya se acaba de
        # descargar. Las tres columnas estaban declaradas, migradas y LEÍDAS (la cabecera
        # del radar y el bloque de abajo), pero **nadie las escribía**: medido 2026-08-12,
        # `release_date_es` era NULL en 21.320 de 21.374 películas (100%) mientras el
        # JSONB crudo estaba al 100%. Eso rompía tres cosas a la vez — la cabecera del
        # radar se quedaba con 4 candidatas, `mark_released_upcoming` no podía detectar
        # ningún flag caducado, y no se podía filtrar por país en local.
        # Coste: CERO llamadas extra, el dato ya viene en este mismo `get_movie_details`.
        rd = movie.release_dates or {}
        if isinstance(rd, dict) and rd:
            def _fecha(valor):
                try:
                    return date.fromisoformat(str(valor)[:10])
                except (TypeError, ValueError):
                    return None
            if rd.get("ES"):
                movie.release_date_es = _fecha(rd["ES"]) or movie.release_date_es
            if rd.get("US"):
                movie.release_date_us = _fecha(rd["US"]) or movie.release_date_us
            # "WW" no existe como clave: el estreno mundial es el MÁS TEMPRANO de todos.
            todas = [d for d in (_fecha(v) for v in rd.values()) if d]
            if todas:
                movie.release_date_ww = min(todas)

        # Disponibilidad en streaming, del mismo detalle. `providers_data` viene en el
        # `append_to_response` de `get_movie_details` y hasta hoy se descartaba, así que
        # `movie_availability` sólo se llenaba de forma REACTIVA (cuando alguien miraba
        # una película). Resultado medido: de los 12.860 candidatos de feed, sólo el 20%
        # tenía disponibilidad fresca según el TTL de 7 días del propio ProviderService.
        # Persistirlo aquí es gratis y es lo que permitirá filtrar por proveedor EN ORIGEN
        # en vez de post-filtrar, que es lo que hoy vacía filas enteras del feed.
        await _guardar_disponibilidad(movie, tmdb_data)

        if movie.is_upcoming:
            today = date.today()
            es_released = movie.release_date_es and movie.release_date_es <= today
            us_released = movie.release_date_us and movie.release_date_us <= today
            ww_released = movie.release_date_ww and movie.release_date_ww <= today
            if es_released or (us_released and not movie.release_date_es) or ww_released:
                movie.is_upcoming = False
                logger.info(f"[Refresh] {movie.title} is now released — marked as non-upcoming")

        movie.last_metadata_refresh = datetime.utcnow()
        return True

    except Exception as e:
        logger.warning(f"Failed to refresh movie {movie.id} ({movie.title}): {e}")
        return False


async def mark_released_upcoming(db: AsyncSession) -> int:
    """Mark upcoming movies that have passed their release date."""
    today = date.today()
    result = await db.execute(
        update(Movie)
        .where(Movie.is_upcoming.is_(True))
        .where(
            or_(
                Movie.release_date_es <= today,
                and_(Movie.release_date_us <= today, Movie.release_date_es.is_(None)),
                Movie.release_date_ww <= today,
            )
        )
        .values(is_upcoming=False)
    )
    await db.commit()
    return result.rowcount


async def run(strategy: str, limit: int, dry_run: bool, force: bool = False) -> None:
    strategies = ["recent", "mid", "classic"] if strategy == "all" else [strategy]

    tmdb = TMDBClient()
    omdb = OMDbClient()

    try:
        if not dry_run:
            async with AsyncSessionLocal() as db:
                released = await mark_released_upcoming(db)
                logger.info(f"[Upcoming sweep] Marked {released} movies as non-upcoming (release date passed)")

        for s in strategies:
            logger.info(f"Strategy: {s} | limit: {limit} | dry_run: {dry_run} | force: {force}")
            async with AsyncSessionLocal() as db:
                movies = await get_movies_to_refresh(db, s, limit, force=force)
                logger.info(f"[{s}] Found {len(movies)} movies due for refresh")

                if dry_run:
                    for m in movies:
                        logger.info(f"  DRY-RUN: would refresh {m.id} {m.title} ({m.year})")
                    continue

                updated = 0
                for movie in movies:
                    ok = await refresh_movie(movie, tmdb, omdb)
                    if ok:
                        updated += 1

                await db.commit()
                logger.info(f"[{s}] Refreshed {updated}/{len(movies)} movies")
    finally:
        await tmdb.aclose()
        await omdb.close()


def main():
    parser = argparse.ArgumentParser(description="Refresh movie metadata from TMDB/OMDb")
    parser.add_argument(
        "--strategy",
        choices=["recent", "mid", "classic", "all"],
        default="recent",
        help="Which age cohort to refresh",
    )
    parser.add_argument("--limit", type=int, default=100, help="Max movies to refresh per run")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be refreshed without updating")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore last_metadata_refresh threshold and re-fetch every film in the cohort. "
             "Use to recover from incomplete refreshes (e.g. when a previous bug skipped a field).",
    )
    args = parser.parse_args()

    asyncio.run(run(args.strategy, args.limit, args.dry_run, force=args.force))


if __name__ == "__main__":
    main()
