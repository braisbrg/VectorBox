"""Does the keyword channel pay for itself? Labelled panel, real pipeline, no LLM.

The lexical channel was added to fix RECALL — films the vector cannot reach
because the query is phrased in today's words and the film's description is not.
The open question was the other side of the trade: whether ORing catalogue
keywords drags irrelevant films into queries the vector already answers. A
generic term like `robbery` (145 films) looks nothing like `found footage` (175)
in the statistics, so no frequency cutoff can tell them apart and the only way to
know was to label results and count.

Method, so the numbers can be trusted:

  * Both configurations run through the REAL `_run_natural_search`, toggled by
    `lexical_channel.ENABLED`. Reimplementing the pipeline in the harness lied
    three times while this was being built — twice by omitting the quality gate,
    which is what made `Collateral Beauty` look like a regression.
  * Labels were written over the UNION of both top-tens, without knowing which
    configuration produced which film. Labelling each row separately is how a
    panel quietly starts agreeing with whoever built it.
  * `relevant` means the film answers the SUBJECT. It says nothing about whether
    the film is any good, which is why `duelo` scores 10/10 either way: every
    film there really is about grief, and what the channel changed was swapping a
    VBS-0 short for Dear Zachary (93) and Drive My Car (85). This metric is blind
    to that improvement — it is a floor, not a verdict.

Measured 2026-07-31: 86 relevant of 100 without, 85 with — no gain, no real
loss. Then the tail undercut the conclusion: past position ten the channel
uniquely admits Star Wars: Episode III and Chronicle of Anna Magdalena Bach to a
giallo query, through the keywords `violence` and `baroque`. So the channel ships
OFF (see lexical_channel.ENABLED) and these tests document what is settled — it
recovers real films, and it does not cost precision in the top ten — while a
labelled top-TWENTY panel decides the rest.

    docker compose exec backend python -m pytest tests/test_lexical_precision.py -m integration -q
"""
import pytest
import pytest_asyncio

from config import AsyncSessionLocal, engine
from routers.search import SearchRequest, _run_natural_search
from services import lexical_channel
from services.embedding_service import EmbeddingService
from services.nlp_search import MovieSearchIntent
from services.qdrant_service import QdrantService
from services.tmdb_client import TMDBClient

pytestmark = pytest.mark.integration


QUERIES = {
    "giallo italiano": dict(
        semantic_query="giallo, stylish murder mystery, lurid, baroque violence",
        countries=["Italy"]),
    "slasher 80s": dict(
        semantic_query="slasher, masked killer, teenagers stalked, gore",
        year_min=1980, year_max=1989),
    "thrillers coreanos": dict(
        semantic_query="thriller, suspense, mystery, crime, tension",
        countries=["South Korea"]),
    "noir 40s": dict(
        semantic_query="film noir, private eye, femme fatale, corruption",
        year_min=1940, year_max=1949),
    "anime 90s": dict(
        semantic_query="anime, hand-drawn animation, japanese, fantastical",
        countries=["Japan"], year_min=1990, year_max=1999),
    "soledad urbana": dict(
        semantic_query="loneliness, isolation, urban alienation, solitude"),
    "atracos 70s": dict(
        semantic_query="heist, stylish, caper, robbery, crime, gangster",
        year_min=1970, year_max=1979),
    "kung fu 70s": dict(
        semantic_query="martial arts, kung fu, duels, revenge, shaolin",
        year_min=1970, year_max=1979),
    "atracos europeos 70s": dict(
        semantic_query="heist, stylish, caper, robbery, crime",
        countries=["France", "Germany", "Italy", "Spain", "United Kingdom"],
        year_min=1970, year_max=1979),
    "duelo": dict(
        semantic_query="grief, mourning, loss, quiet sorrow, contemplative"),
}

# tmdb_id -> answers the subject. Unlabelled ids count as irrelevant, so a film
# that drifts into the row later fails loudly instead of scoring for free.
RELEVANT = {
    "giallo italiano": {28055, 20126, 20115, 11906, 29702, 20345},
    "slasher 80s": {377, 10131, 40952, 9730, 39874, 47886, 13567, 40969, 24124, 13555, 27475},
    "thrillers coreanos": {269494, 973628, 705996, 838209, 488623, 849869, 432836,
                           165213, 4689, 581528},
    "noir 40s": {26038, 996, 1939, 37992, 17058, 14638, 17801, 25736, 17136, 16703},
    "anime 90s": {26945, 39323, 44251, 39100, 39108, 39148, 42994, 21057, 128, 125521},
    "soledad urbana": {662400, 31026, 27102, 57209, 21135, 102001, 153, 57564,
                       11985, 24166, 144},
    "atracos 70s": {968, 336, 5854, 65066, 11657, 15371, 2153, 11583, 9277, 31656},
    "kung fu 70s": {9461, 11713, 21964, 13333, 49636, 11537, 11841, 9462, 96144},
    "atracos europeos 70s": {336, 4031, 11657, 993, 11583, 11843, 42741},
    "duelo": {34653, 15584, 758866, 352197, 577084, 334541, 315872, 465672, 56978,
              404141, 16619, 749004, 202506, 668091, 4496, 131836, 145147},
}

# One film in one query flipped when this was measured (soledad urbana, 10 -> 9).
# The bar allows that much drift and no more: the panel is ten queries, so a real
# regression from ORing keywords would cost far more than a single row.
MAX_PRECISION_LOSS = 2


@pytest_asyncio.fixture(autouse=True)
async def _fresh_pool():
    """Restores whatever the module shipped with, not a hardcoded True: the
    channel is currently off by default, and a test that silently switched it on
    for everyone after it would be worse than no test."""
    default = lexical_channel.ENABLED
    await engine.dispose()
    yield
    lexical_channel.ENABLED = default
    await engine.dispose()


async def _top10(label, kwargs):
    intent = MovieSearchIntent(reasoning="forced (precision panel)", **kwargs)
    async with AsyncSessionLocal() as db:
        resp = await _run_natural_search(
            SearchRequest(query=label, forced_intent=intent),
            None, db, TMDBClient(), QdrantService(), EmbeddingService(),
        )
    return [m["movie_id"] for m in (resp.results or [])[:10]]


async def _hits(label, kwargs, enabled):
    """(relevant count, ids). The recall cases below carry no labels — they ask
    whether ONE known film comes back, not how many — so a missing label set
    counts zero rather than raising."""
    lexical_channel.ENABLED = enabled
    ids = await _top10(label, kwargs)
    return sum(1 for i in ids if i in RELEVANT.get(label, set())), ids


@pytest.mark.asyncio
async def test_keyword_channel_does_not_cost_precision():
    """The whole panel at once: ORing catalogue keywords must not drag junk into
    the queries the vector already answers."""
    off = on = 0
    per_query = []
    for label, kwargs in QUERIES.items():
        a, _ = await _hits(label, kwargs, False)
        b, _ = await _hits(label, kwargs, True)
        off += a
        on += b
        per_query.append(f"{label}: {a}->{b}")
    assert on >= off - MAX_PRECISION_LOSS, (
        f"el canal lexico degrada la precision ({off} -> {on}): " + ", ".join(per_query)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("label,kwargs,target,title", [
    ("zombis 40s",
     dict(semantic_query="zombie outbreak, undead, survival horror, infection",
          year_min=1940, year_max=1949),
     27130, "I Walked with a Zombie"),
    ("found footage 60s",
     dict(semantic_query="found footage, handheld camera, first person horror",
          year_min=1960, year_max=1969),
     # `The War Game` was the first candidate here and this test's own guard
     # rejected it: the vector already returned it, lower down. The claim it was
     # missing came from reading a top-six instead of the whole row.
     85692, "A Man Vanishes"),
])
async def test_keyword_channel_recovers_what_the_vector_misses(label, kwargs, target, title):
    """The other half of the trade, and the reason the channel exists.

    Both films sit in the catalogue carrying the exact keyword the query names,
    and the vector cannot reach them: a 1943 film about voodoo and dread does not
    read like "zombie outbreak, infection", and a 1966 BBC docudrama does not read
    like "handheld camera, first person horror".
    """
    _, without = await _hits(label, kwargs, False)
    _, with_ = await _hits(label, kwargs, True)
    assert target not in without, (
        f"{title} ya salia sin el canal lexico — este test ya no prueba nada"
    )
    assert target in with_, f"{title} sigue perdido con el canal lexico activo"
