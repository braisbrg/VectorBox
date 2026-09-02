"""Ficha de director: lo básico y su filmografía.

Existe porque la barra de búsqueda no tenía a dónde llevar. Teclear "kurosawa"
devolvía el documental *Kurosawa* (2000) —que es una respuesta legítima, no un
fallo— mezclado con como mucho ocho de sus treinta películas, bajo un tope duro
de doce filas y sin scroll. El desplegable hacía bien su trabajo; lo que faltaba
era el destino.

Sin tabla nueva: `Movie.directors` es un array poblado en el 99% del catálogo
(20.219 de 20.418 filas, 9.331 directores distintos). Una ficha de director es
una consulta, no un modelo de datos.

ponytail: sin biografía ni foto. Eso vive en el endpoint /person de TMDB y son
otra llamada, otra caché y otro fallo posible. Se añade cuando alguien lo pida.
"""
import logging
from datetime import timedelta
from typing import Optional

import orjson
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_db
from dependencies import get_optional_current_user, get_redis, get_tmdb_client
from limiter import limiter
from models.database import Movie, UserRating
from models.schemas import TokenResponse
# La misma plegadura que usan los títulos (unaccent + lower + puntuación). Se
# importa en vez de copiarse: dos definiciones de "el mismo nombre" se separan.
from routers.search import _match_expression, _normalize_needle
from services.tmdb_client import TMDBClient

logger = logging.getLogger(__name__)
router = APIRouter()

# Una filmografía cabe entera en una página en casi todos los casos (el máximo
# del catálogo ronda las decenas), pero el parámetro existe porque el problema
# que resuelve esta ruta ERA un tope duro. Un tope que no se puede pasar es el
# bug; uno que sí, es paginación.
PAGE_MAX = 100

# Por debajo de esto NO se compara tu media con este director contra tu media
# global. Con dos películas, «+1.2★ sobre tu media» es ruido, no un gusto — es la
# misma trampa que en /me/profile hacía que Anne Hathaway (2 películas) le ganase
# a Nolan (7). Allí se resolvió con shrinkage porque había que ordenar una lista;
# aquí sólo hay que decidir si se enseña un número, y un suelo basta.
MIN_FILMS_TO_COMPARE = 3

# Caché PARTIDA: lo público se comparte, lo personal nunca se guarda.
#
# El reparto está medido (2026-08-10), y no era donde yo pensaba: la bio de TMDB
# tarda 2 ms porque `get_person` ya la cachea 30 días, mientras que las consultas
# públicas de Postgres cuestan 57 ms en caliente y 167 en frío. Lo que se cachea
# es eso, y sólo eso.
#
# ⚠ RE-MEDIDO el 2026-09-01, tras pasar a buscar por nombre plegado: la ruta en
# frío va a **430-548 ms** (los 167 de arriba ya no se reproducen). El desglose,
# con EXPLAIN ANALYZE en caliente sobre 21.350 filas:
#
#   `'Akira Kurosawa' = any(directors)`   Seq Scan   37-43 ms
#   EXISTS + unnest + unaccent/lower       Seq Scan   94-97 ms
#
# Los DOS son Seq Scan: **no hay ningún índice sobre `directors`** (el único GIN
# de la tabla es `ix_movies_genres_gin`), así que plegar no perdió un índice —
# nunca lo hubo. Lo que se paga es la función por fila, y no se puede indexar
# porque `unaccent()` es STABLE, no IMMUTABLE (misma razón por la que tampoco lo
# hace `routers/search.py`). A 21k filas se asume; si el catálogo crece, la
# salida barata es probar primero la igualdad exacta —que es lo que manda el
# autocompletado— y caer al plegado sólo cuando no encuentra nada.
#
# El bloque `library` queda FUERA a propósito: mete "has visto 25/47" y "la mejor
# que te falta" en la respuesta, así que compartirlo entre usuarios no sería un
# bug de refresco sino una fuga de datos. Se recalcula siempre (18-33 ms).
DIRECTOR_CACHE_TTL = timedelta(hours=6)
# Súbela si cambia la FORMA del payload público: una clave vieja con campos
# nuevos ausentes rompe el frontend durante 6 horas sin dejar rastro.
DIRECTOR_CACHE_VERSION = "v1"


# Por popularidad por defecto: al abrir la ficha de alguien con treinta películas
# lo que se busca es reconocer algo, y el orden cronológico deja arriba lo último
# que hizo, que suele ser lo menos conocido.
SORTS = {
    "popularity": (Movie.popularity, True),
    "year": (Movie.year, True),
    "score": (Movie.vectorbox_score, True),
    "title": (Movie.title, False),
}


def is_director(needle: str):
    """EXISTS: alguna entrada de `directors` pliega exactamente a `needle`.

    EXACTO sobre el elemento, no `ilike` sobre el array: "Anderson" no puede
    arrastrar a Wes, a Paul Thomas y a Brad a la misma ficha. Pero exacto
    *después de plegar*, que es lo que faltaba — `Movie.directors.any(name)`
    comparaba byte a byte, así que "akira kurosawa" y "Léos Carax" en minúsculas
    o sin tildes daban 404 mientras la grafía de TMDB daba 200.

    `unnest` como tabla derivada correlacionada: el fold se aplica a CADA nombre
    del array, no a su concatenación.
    """
    d = func.unnest(Movie.directors).column_valued("d")
    return select(1).where(_match_expression(d) == needle).exists()


def director_shape(rows: list, name: str = "") -> dict:
    """Reparto recurrente, géneros, periodo y Q media de una filmografía.

    `rows` es [(cast, genres, year, vectorbox_score), ...] de TODAS sus películas,
    no de la página: un reparto recurrente calculado sobre las 50 más populares
    diría que Scorsese usa a DiCaprio más que a De Niro.

    Puro, para poder comprobarlo sin base de datos.
    """
    actors: dict = {}
    genres: dict = {}
    years: list = []
    scores: list = []
    for cast, film_genres, year, score in rows:
        for a in cast or []:
            actors[a] = actors.get(a, 0) + 1
        for g in film_genres or []:
            genres[g] = genres.get(g, 0) + 1
        if year:
            years.append(year)
        if score is not None:
            scores.append(score)
    return {
        # Uno solo no es recurrencia: sale a partir de dos películas juntos. Y el
        # propio director se cae — sale acreditado en sus documentales y "Scorsese
        # colabora repetidamente con Scorsese" no le dice nada a nadie.
        "recurring_cast": [
            {"name": a, "films": n}
            for a, n in sorted(actors.items(), key=lambda kv: -kv[1])
            if n >= 2 and a != name
        ][:8],
        "genres": [{"name": g, "films": n} for g, n in sorted(genres.items(), key=lambda kv: -kv[1])[:5]],
        "span": {"from": min(years), "to": max(years)} if years else None,
        "avg_score": round(sum(scores) / len(scores), 1) if scores else None,
    }


def library_shape(rows: list, global_avg: Optional[float]) -> dict:
    """Tu relación con esta filmografía: vistas, cómo la puntúas y qué te falta.

    `rows` es [(tmdb_id, title, year, poster_path, score, rating, is_watched,
    is_watchlist, is_upcoming), ...] de TODAS sus películas, vistas o no.

    Puro, para poder comprobarlo sin base de datos.
    """
    seen = [r for r in rows if r[6]]
    ratings = [r[5] for r in seen if r[5] is not None]
    avg = round(sum(ratings) / len(ratings), 2) if ratings else None

    # La mejor que te falta: mayor Q sin ver. Es lo único de este bloque que no
    # es una estadística sino una recomendación, y es lo que la app hace. Las no
    # estrenadas quedan fuera: la mejor pendiente de Nolan salía «The Odyssey»
    # (2026, Q 90.4), que no se puede ver. Siguen contando en el total.
    unseen = [r for r in rows if not r[6] and r[4] is not None and not r[8]]
    best = max(unseen, key=lambda r: r[4]) if unseen else None

    return {
        "seen": len(seen),
        "rated": len(ratings),
        "watchlisted": sum(1 for r in rows if r[7] and not r[6]),
        "avg_rating": avg,
        # Sólo con suelo Y con una media global contra la que comparar.
        "vs_your_avg": (
            round(avg - global_avg, 2)
            if avg is not None and global_avg is not None and len(ratings) >= MIN_FILMS_TO_COMPARE
            else None
        ),
        "best_unseen": (
            {"tmdb_id": best[0], "title": best[1], "year": best[2],
             "poster_path": best[3], "vectorbox_score": best[4]}
            if best else None
        ),
    }


@router.get("/{name}")
@limiter.limit("60/minute")
async def director_films(
    request: Request,
    name: str,
    sort: str = Query("popularity"),
    limit: int = Query(50, ge=1, le=PAGE_MAX),
    offset: int = Query(0, ge=0),
    lang: str = Query("en"),
    db: AsyncSession = Depends(get_db),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    redis=Depends(get_redis),
    # Opcional a propósito: la ficha es útil sin sesión y así invitados y
    # registrados comparten endpoint, igual que /onboarding/movies.
    current_user: Optional[TokenResponse] = Depends(get_optional_current_user),
):
    """Películas de un director, de más nueva a más antigua.

    El nombre se compara EXACTO contra el array: los nombres vienen de TMDB, ya
    están normalizados, y un `ilike` aquí haría que "Anderson" arrastrase a Wes,
    a Paul Thomas y a Brad a la misma ficha.

    La respuesta se monta en dos mitades que NUNCA se mezclan en la caché: lo
    público (compartido, 6 h) y `library` (por usuario, siempre recalculado).
    """
    # El user_id NO entra en la clave: si entrase, la caché sería por usuario y
    # dejaría de ahorrar nada. Es seguro justamente porque `library` se añade
    # DESPUÉS de leerla, nunca dentro.
    #
    # La clave es el NOMBRE PLEGADO, el mismo que busca la consulta. Antes era
    # `name.lower()` contra una consulta exacta: las variantes compartían caché
    # pero no resultado, así que "akira kurosawa" daba 404 en frío y 200 si
    # "Akira Kurosawa" había pasado antes. Ahora las variantes comparten las dos
    # cosas — una entrada de caché por director, no por grafía.
    needle = _normalize_needle(name)
    cache_key = (f"director:{DIRECTOR_CACHE_VERSION}:{lang}:"
                 f"{needle}:{sort}:{limit}:{offset}")
    public = None
    try:
        cached = await redis.get(cache_key)
        if cached:
            public = orjson.loads(cached)
    except Exception as e:
        logger.warning("director cache read failed for %r: %s", name, e)

    if public is None:
        public = await _build_public(needle, sort, limit, offset, lang, db, tmdb)
        try:
            await redis.setex(cache_key, DIRECTOR_CACHE_TTL, orjson.dumps(public))
        except Exception as e:
            logger.warning("director cache write failed for %r: %s", name, e)

    library = None
    if current_user:
        library = await _build_library(needle, current_user.user_id, db)

    # `library` se inyecta aquí y sólo aquí. Mientras siga siendo la última
    # línea, ningún dato personal puede acabar en una clave compartida.
    return {**public, "library": library}


async def _build_public(needle: str, sort: str, limit: int, offset: int,
                        lang: str, db: AsyncSession, tmdb: TMDBClient) -> dict:
    """La mitad compartible: filmografía, forma de la obra y ficha de TMDB.

    Recibe el nombre YA PLEGADO. La grafía que se devuelve y con la que se
    pregunta a TMDB es la del catálogo, no la que tecleó el usuario: si no,
    `/directors/akira kurosawa` respondería `"name": "akira kurosawa"` y el
    filtro `a != name` de `director_shape` dejaría de tirar al propio director
    de su reparto recurrente.
    """
    base = (
        select(Movie)
        .where(is_director(needle))
        .where(Movie.is_excluded.is_(False))
    )

    total = await db.scalar(
        select(func.count()).select_from(base.subquery())
    )
    if not total:
        raise HTTPException(status_code=404, detail="Director not found")

    column, descending = SORTS.get(sort, SORTS["popularity"])
    ordering = column.desc().nullslast() if descending else column.asc()
    rows = (await db.execute(
        base.order_by(ordering, Movie.title).limit(limit).offset(offset)
    )).scalars().all()

    # Sobre la filmografía entera, no sobre `rows` — ver director_shape.
    shape_rows = (await db.execute(
        select(Movie.cast, Movie.genres, Movie.year, Movie.vectorbox_score,
               Movie.directors)
        .where(is_director(needle))
        .where(Movie.is_excluded.is_(False))
    )).all()

    # La grafía real, de la propia filmografía: sale gratis de filas que ya
    # tenemos. Sobre `shape_rows` y no sobre `rows`, que puede venir vacía con
    # un offset pasado del final.
    name = next(
        (d for r in shape_rows for d in (r[4] or []) if _normalize_needle(d) == needle),
        needle,
    )

    # La ficha se sirve aunque TMDB no conteste: la filmografía sale de nuestra
    # base de datos y es lo que la página existe para mostrar.
    try:
        person = await tmdb.get_person(name, lang=lang)
    except Exception as e:
        logger.warning("TMDB person lookup failed for %r: %s", name, e)
        person = None

    return {
        "name": name,
        "total": total,
        "sort": sort if sort in SORTS else "popularity",
        **director_shape([r[:4] for r in shape_rows], name),
        "profile_path": (person or {}).get("profile_path"),
        "biography": (person or {}).get("biography"),
        "birthday": (person or {}).get("birthday"),
        "place_of_birth": (person or {}).get("place_of_birth"),
        "offset": offset,
        "films": [
            {
                "tmdb_id": m.tmdb_id,
                "title": m.title,
                "title_es": m.title_es,
                "year": m.year,
                "poster_path": m.poster_path,
                "vectorbox_score": m.vectorbox_score,
                "popularity": m.popularity,
                "genres": m.genres or [],
            }
            for m in rows
        ],
    }


async def _build_library(needle: str, user_id: int, db: AsyncSession) -> dict:
    """La mitad personal. NUNCA se cachea: lleva cuántas has visto y cuál te falta.

    Compartir esto entre usuarios no sería un bug de refresco sino una fuga de
    datos, así que vive en su propia función y se inyecta después de la caché.

    LEFT JOIN para que las que NO has visto sigan en el resultado: son la mitad
    del bloque (`best_unseen`).
    """
    lib_rows = (await db.execute(
        select(Movie.tmdb_id, Movie.title, Movie.year, Movie.poster_path,
               Movie.vectorbox_score, UserRating.rating, UserRating.is_watched,
               UserRating.is_watchlist, Movie.is_upcoming)
        .outerjoin(UserRating, and_(
            UserRating.movie_id == Movie.id,
            UserRating.user_id == user_id,
        ))
        .where(is_director(needle))
        .where(Movie.is_excluded.is_(False))
    )).all()
    global_avg = await db.scalar(
        select(func.avg(UserRating.rating)).where(
            UserRating.user_id == user_id,
            UserRating.rating.isnot(None),
        )
    )
    return library_shape(lib_rows, float(global_avg) if global_avg is not None else None)
