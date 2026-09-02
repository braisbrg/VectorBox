from pydantic import BaseModel, Field
from typing import List, Optional, Any

# --- OMDb Schemas ---

class OMDbRating(BaseModel):
    Source: str
    Value: str

class OMDbResponse(BaseModel):
    Response: str
    Error: Optional[str] = None
    Title: Optional[str] = None
    Year: Optional[str] = None
    imdbRating: Optional[str] = None
    imdbVotes: Optional[str] = None  # e.g. "1,234,567" — parsed to int by callers
    Ratings: List[OMDbRating] = Field(default_factory=list)
    Metascore: Optional[str] = None
    Rated: Optional[str] = None       # MPAA: G/PG/PG-13/R/NC-17/NR/TV-14
    Awards: Optional[str] = None      # raw "Won 3 Oscars. 33 wins & 41 nominations total"
    Country: Optional[str] = None     # comma-separated countries
    Language: Optional[str] = None    # comma-separated languages

    class Config:
        extra = "ignore"

class VectorBoxBreakdown(BaseModel):
    imdb: Optional[float] = None
    meta: Optional[int] = None
    tmdb: Optional[float] = None

class VectorBoxScore(BaseModel):
    score: Optional[float] = None
    breakdown: VectorBoxBreakdown = Field(default_factory=VectorBoxBreakdown)

# --- Qdrant Schemas ---

class QdrantPayload(BaseModel):
    tmdb_id: int
    title: str
    year: Optional[int] = None
    genres: List[str] = Field(default_factory=list)
    overview: Optional[str] = None
    poster_path: Optional[str] = None
    rating: Optional[float] = None  # This maps to vote_average
    vote_count: Optional[int] = None
    runtime: Optional[int] = None
    original_language: Optional[str] = None
    keywords: List[str] = Field(default_factory=list)
    
    # Extended Metrics
    vectorbox_score: Optional[float] = None
    imdb_rating: Optional[float] = None
    metacritic_rating: Optional[int] = None
    
    # Spanish Metadata
    title_es: Optional[str] = None
    overview_es: Optional[str] = None
    
    # Credits
    directors: List[str] = Field(default_factory=list)
    cast: List[str] = Field(default_factory=list)

    # Enriched-vector gate flag (recommendation surfaces filter on this).
    # Optional so legacy callers don't break, but populate it explicitly:
    # model_dump(exclude_none=True) DROPS it when None, and an absent key
    # excludes the point from all gated searches.
    has_enriched_embedding: Optional[bool] = None

    # Filterable dimensions, added 2026-07-29. Qdrant narrows on these DURING the
    # search; before they lived here they were enforced in Postgres afterwards,
    # which starved them — "thrillers coreanos" kept 1 film of 219 Korean ones.
    countries: List[str] = Field(default_factory=list)
    spoken_languages: List[str] = Field(default_factory=list)
    mpaa_rating: Optional[str] = None
    oscar_wins: Optional[int] = 0
    is_adult: Optional[bool] = False

    class Config:
        extra = "ignore"


def qdrant_payload(movie, *, enriched: Optional[bool] = None, **overrides) -> dict:
    """THE canonical Qdrant payload. Every writer must go through this.

    `upsert_movie_vector` REPLACES the payload, so a writer that omits a key
    deletes it from that point — silently, with no error, and the film simply
    stops matching any filter on it. CLAUDE.md has warned about this shape for
    `has_enriched_embedding` since 2026-07-13; on 2026-07-30 an audit found five
    writers building the dict inline and dropping the five new dimensions, among
    them `get_or_create_movie` — the path every newly ingested film takes.

    So there is one builder now rather than three (this, a copy in
    scripts/reembed_catalog.py, and five hand-rolled dicts), and
    tests/test_qdrant_payload_writers.py fails if a call site stops using it.

    `enriched` overrides the row flag for callers that build the payload BEFORE
    flipping has_enriched_embedding on the row (enrich_vectors/check_embeddings
    ordering).
    """
    return {
        "tmdb_id": movie.tmdb_id,
        "title": movie.title,
        "year": movie.year,
        "genres": movie.genres or [],
        "overview": movie.overview or "",
        "poster_path": movie.poster_path,
        "vote_average": movie.vote_average,
        "vote_count": movie.vote_count,
        "runtime": movie.runtime,
        "original_language": movie.original_language,
        "keywords": movie.keywords or [],
        "directors": movie.directors,
        "cast": movie.cast,
        "vectorbox_score": movie.vectorbox_score,
        "imdb_rating": movie.imdb_rating,
        "metacritic_rating": movie.metacritic_rating,
        "title_es": movie.title_es,
        "overview_es": movie.overview_es,
        "has_enriched_embedding": (
            bool(movie.has_enriched_embedding) if enriched is None else enriched
        ),
        "countries": movie.omdb_countries or [],
        "spoken_languages": movie.omdb_languages or [],
        "mpaa_rating": movie.mpaa_rating,
        "oscar_wins": movie.oscar_wins or 0,
        "is_adult": bool(movie.is_adult),
        # Ejes de mood. Se estampan aparte (compute_mood_axes.py proyecta el
        # vector), pero viven en una COLUMNA, así que tienen que viajar aquí
        # también: sin esto cada re-upsert los borraba del punto y la película
        # dejaba de existir para los chips de ánimo, en silencio. Medido
        # 2026-08-19: 38 puntos sin ejes en Qdrant contra 31 filas sin valor en
        # Postgres — los 7 de diferencia los había vaciado un re-upsert.
        "mood_gravedad": movie.mood_gravedad,
        "mood_humanidad": movie.mood_humanidad,
        # For values a caller holds fresher than the row — enrich_vectors fetches
        # keywords from TMDB and upserts before persisting them.
        **overrides,
    }
