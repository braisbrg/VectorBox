"""End-to-end search quality, against the real catalogue. No LLM.

`verify_search_branches.py` pins WHICH BRANCH answers. This pins WHAT COMES
BACK: that a filtered query is not padded with the least-bad content of a small
box, that a narrow-but-real request still gets a row, and that naming a film
lands on the right film.

Every case drives the real `_run_natural_search` through `forced_intent`, so the
parser never runs and the results are repeatable on any Groq budget. The
intents are ones the parser has been observed to produce.

Marked `integration` (deselected by default — see pytest.ini) because it needs
Postgres, Qdrant and the embedding model:

    docker compose exec backend python -m pytest tests/test_search_quality_panel.py -m integration -q

Assertions are RANGES, not fixed titles, wherever the catalogue can drift. The
reference-lookup cases are exact because those are the regression this file was
opened for: "james bond" answering with a Daniel Craig documentary.
"""
import pytest
import pytest_asyncio

from config import AsyncSessionLocal, engine
from routers.search import SearchRequest, _pick_reference_movie, _run_natural_search
from services.embedding_service import EmbeddingService
from services.magic_search_ranking import SEARCH_RESULT_LIMIT
from services.nlp_search import MovieSearchIntent
from services.qdrant_service import QdrantService
from services.tmdb_client import TMDBClient

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture(autouse=True)
async def _fresh_pool():
    """Return the pooled asyncpg connections before the loop that owns them dies.

    `config.engine` is module-level and pytest-asyncio gives each test its own
    event loop, so the second test to touch Postgres inherited a connection
    bound to the first test's dead loop — "got Future attached to a different
    loop", surfacing as a 500 from inside the route. A product-looking failure
    with a harness-shaped cause.

    Disposes on BOTH sides: after, so these tests leak nothing onwards; before,
    because another module's integration test may already have left a connection
    on a dead loop — running this file alone passed while `pytest -m integration`
    over the whole suite failed, which is the signature of inherited state.
    """
    await engine.dispose()
    yield
    await engine.dispose()


async def _search(**intent_kwargs):
    intent = MovieSearchIntent(reasoning="forced (quality panel)", **intent_kwargs)
    async with AsyncSessionLocal() as db:
        resp = await _run_natural_search(
            SearchRequest(query=intent_kwargs["semantic_query"], forced_intent=intent),
            None, db, TMDBClient(), QdrantService(), EmbeddingService(),
        )
    return resp.results or []


# ── the guarantee: no request comes back empty ───────────────────────────────
#
# The relevance cliff shortens rows, and a short row is fine. A blank page is
# not: it is the failure this codebase has already paid for twice (refusing
# "no se que ver", refusing "algo corto de menos de 90 minutos"). Every shape
# of request the engine supports is listed here, answerable or not.
ALWAYS_ANSWERED = [
    ("tema puro", dict(semantic_query="grief, mourning, loss, quiet sorrow")),
    ("tema + epoca", dict(semantic_query="heist, caper, robbery, crime",
                          year_min=1970, year_max=1979)),
    ("tema + pais", dict(semantic_query="thriller, suspense, mystery, crime",
                         countries=["South Korea"])),
    ("epoca imposible", dict(semantic_query="cyberpunk, neon, hackers, dystopia",
                             year_min=1950, year_max=1959)),
    ("interseccion vacia", dict(semantic_query="heist, robbery, caper, crime",
                                countries=["France", "Italy", "Spain"],
                                year_min=1970, year_max=1979)),
    ("peticion abierta", dict(semantic_query="no se que ver", open_request=True)),
    ("solo calidad", dict(semantic_query="muy bien valoradas", min_vectorbox_score=75)),
]


@pytest.mark.parametrize("label,intent", ALWAYS_ANSWERED, ids=[c[0] for c in ALWAYS_ANSWERED])
@pytest.mark.asyncio
async def test_every_supported_request_returns_films(label, intent):
    results = await _search(**intent)
    assert results, f"{label}: pagina en blanco"
    assert len(results) <= SEARCH_RESULT_LIMIT


# ── the cliff earns its place ────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_overconstrained_query_is_trimmed_not_padded():
    """"European + 1970s + heists" has ~5 answers in the catalogue. Before the
    cliff it returned a full row of twenty, of which two were heists.

    The row may still reach twenty — the safety net tops it up — but every film
    past the box has to SAY it is past the box. Padding is unmarked filler; the
    top-up is labelled, and that is the whole difference.
    """
    results = await _search(
        semantic_query="heist, robbery, caper, crime",
        countries=["France", "Germany", "Italy", "Spain", "United Kingdom"],
        year_min=1970, year_max=1979,
    )
    assert results
    inside = [r for r in results if not r.get("outside_filters")]
    assert 0 < len(inside) < SEARCH_RESULT_LIMIT, (
        "the films matching EVERY filter must not fill twenty slots"
    )
    for r in results[len(inside):]:
        assert r.get("outside_filters"), f"relleno sin marcar: {r['title']}"


@pytest.mark.asyncio
async def test_answerable_query_keeps_a_full_row():
    """The cliff must not punish a query the catalogue answers well. Italian
    giallo is the densest corner of this catalogue — if anything survives, this
    does."""
    results = await _search(
        semantic_query="giallo, stylish murder mystery, lurid, baroque violence",
        countries=["Italy"],
    )
    assert len(results) >= 10, f"fila sana recortada a {len(results)}"


@pytest.mark.asyncio
async def test_narrow_but_real_request_still_answers():
    """Narrow is not the same as unanswerable: measured, this keeps 9 of 20 and
    every one of them is a martial-arts film."""
    results = await _search(
        semantic_query="martial arts, kung fu, duels, revenge, shaolin",
        year_min=1970, year_max=1979,
    )
    assert len(results) >= 5


# ── naming a film lands on the right film ────────────────────────────────────
#
# Exact titles on purpose. `.first()` on an unordered SELECT made every one of
# these a coin flip across 811 duplicated catalogue titles.
REFERENCE_CASES = [
    ("james bond", True, "James Bond Collection"),   # via collection_name
    ("el padrino", True, "The Godfather"),           # via title_es
    ("la naranja mecanica", True, "A Clockwork Orange"),  # via accent folding
    ("deprisa deprisa", True, "Faster, Faster"),     # via punctuation folding
    ("Mother", False, "Mother"),                     # 6 films share this title
    ("The Hunt", False, "The Hunt"),                 # 5 share this one
]


@pytest.mark.parametrize("needle,substring,expected", REFERENCE_CASES,
                         ids=[c[0] for c in REFERENCE_CASES])
@pytest.mark.asyncio
async def test_reference_lookup_picks_the_best_match(needle, substring, expected):
    async with AsyncSessionLocal() as db:
        movie = await _pick_reference_movie(db, needle, substring=substring)
    assert movie is not None, f"{needle!r}: sin match"
    if expected.endswith("Collection"):
        assert movie.collection_name == expected
    else:
        assert movie.title == expected


@pytest.mark.asyncio
async def test_duplicate_titles_resolve_to_the_best_scored():
    """"Mother" is Bong Joon-ho (VBS 86), Pudovkin 1926 (65) and four others.
    Whichever row Postgres happened to yield first is not an answer."""
    async with AsyncSessionLocal() as db:
        best = await _pick_reference_movie(db, "Mother", substring=False)
        hunt = await _pick_reference_movie(db, "The Hunt", substring=False)
    assert best.year == 2009
    assert hunt.year == 2012


@pytest.mark.asyncio
async def test_no_unreleased_film_is_ever_recommended():
    """`Untitled Ocean's Prequel (2027)` shipped in a top-twelve. Placeholder
    rows carry is_upcoming=False AND a null year, so both guards are needed."""
    results = await _search(semantic_query="heist, caper, robbery, slick crime")
    async with AsyncSessionLocal() as db:
        from sqlalchemy import select
        from models.database import Movie
        ids = [r["movie_id"] for r in results]
        rows = (await db.execute(
            select(Movie.title, Movie.is_upcoming, Movie.year)
            .where(Movie.tmdb_id.in_(ids))
        )).all()
    offenders = [r.title for r in rows if r.is_upcoming or r.year is None]
    assert not offenders, f"sin estrenar en resultados: {offenders}"


# ── the safety net: keep the subject, relax the modifier ─────────────────────
@pytest.mark.asyncio
async def test_impossible_era_still_answers_the_subject():
    """Cyberpunk did not exist in the 1950s — the genre starts around 1982, and
    the catalogue's three nearest 1950s films (Forbidden Planet, The War of the
    Worlds, On the Beach) are not it. That is not a retrieval failure: the films
    are not in the world. What the person wants is cyberpunk, so the era gets
    dropped and real cyberpunk is appended, labelled."""
    results = await _search(
        semantic_query="cyberpunk, neon, hackers, megacorporations, dystopia",
        year_min=1950, year_max=1959,
    )
    outside = [r for r in results if r.get("outside_filters")]
    assert outside, "una peticion irrealizable debe recuperar el tema"
    assert all(r["outside_filters"] == "era" for r in outside)
    assert any(r["year"] and r["year"] > 1979 for r in outside), (
        "el rescate debe traer cine real del genero, no mas de los 50"
    )


@pytest.mark.asyncio
async def test_in_box_films_always_rank_above_the_relaxed_ones():
    """A film that met every constraint must never be pushed down by one that
    ignored the era, however well it scores."""
    results = await _search(
        semantic_query="zombie outbreak, undead, survival horror",
        year_min=1940, year_max=1949,
    )
    flags = [bool(r.get("outside_filters")) for r in results]
    assert flags == sorted(flags), "una relajada se colo por delante de una exacta"


@pytest.mark.asyncio
async def test_a_healthy_row_is_never_topped_up():
    """The net triggers on the LENGTH of the row, so a query the catalogue
    answers must come back untouched. Measured: this keeps 9 in-box films and
    every one is a martial-arts film."""
    results = await _search(
        semantic_query="martial arts, kung fu, duels, revenge, shaolin",
        year_min=1970, year_max=1979,
    )
    assert not any(r.get("outside_filters") for r in results)


# ── one number, one meaning ──────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_branches_without_a_query_vector_report_no_distance():
    """The catalogue branch used to send `vectorbox_score` in `score`, which the
    UI renders as distance to the query. "no se que ver" therefore came back at
    99 while a precise, correctly-answered query showed 83 — vague requests
    outscoring exact ones."""
    results = await _search(semantic_query="no se que ver", open_request=True)
    assert results
    assert all(r["score"] is None for r in results)


@pytest.mark.asyncio
async def test_relevance_is_comparable_across_queries():
    """The displayed score is relevance, so a query the catalogue answers well
    must outscore one it answers badly. With the VBS weight still in the number,
    this was inverted: an acclaimed non-answer beat a precise match."""
    dense = await _search(
        semantic_query="giallo, stylish murder mystery, lurid", countries=["Italy"])
    thin = await _search(
        semantic_query="cyberpunk, neon, hackers, megacorporations, dystopia",
        year_min=1950, year_max=1959)
    thin_inside = [r for r in thin if not r.get("outside_filters")]
    assert max(r["score"] for r in dense) > max(r["score"] for r in thin_inside)
