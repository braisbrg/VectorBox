"""TST-2 — golden-set hits@k regression for the production Qdrant index.

Pins the recall of seven curated anchors against the embeddinggemma-300m
catalog (snapshot 2026-05-15). The test queries the real Qdrant collection
and counts how many of each anchor's hand-curated thematic neighbours show
up in its top-K Qdrant query.

Why this exists:
  - Detect silent quality regressions when changing embedding model,
    enrichment prompt, reference-text recipe, or Qdrant config.
  - A/B-test future moves (e.g. Gemini-Flash enrichment, INT8 quantization
    — see T-16) against a stable baseline number.
  - Provides empirical floor for "did the catalog still produce sensible
    neighbours?" beyond what unit tests can cover.

Why this might be skipped:
  - The test requires a populated Qdrant + Postgres with the catalog —
    same dependencies as the integration suite. CI without those services
    must skip.
  - Marked `slow` because it loads the embedding model + does 7 Qdrant
    round-trips. Local docker run ≤ a few seconds.

Calibration:
  - The current production index returns 13/61 hits@20 on the curated
    anchors. We assert >= 10 to allow -3 drift, which catches a real
    regression (model change, broken re-embed, half-empty catalog) while
    tolerating the noise of single-film tweaks. Tighten when we have
    stable history.
"""
import asyncio
import pytest

pytest_plugins = ("pytest_asyncio",)

from sqlalchemy import or_, select

from config import AsyncSessionLocal
from models.database import Movie
from services.qdrant_service import QdrantService

try:
    from qdrant_client.models import SearchParams
except ImportError:
    SearchParams = None  # type: ignore


_ANCHORS_AND_NEIGHBOURS = {
    "Howl's Moving Castle": [
        "Spirited Away", "Castle in the Sky", "Princess Mononoke",
        "Mary and the Witch's Flower", "Ponyo", "Kiki's Delivery Service",
        "The Cat Returns", "From Up on Poppy Hill", "The Wind Rises",
        "My Neighbor Totoro", "Earwig and the Witch",
    ],
    "Deprisa, deprisa": [
        "Navajeros", "Perros callejeros", "El pico", "Yo, 'El Vaquilla'",
        "El Lute: camina o revienta", "Maravillas", "Colegas",
        "El pico 2", "Barrio", "Los olvidados",
    ],
    "Pan's Labyrinth": [
        "The Devil's Backbone", "The Shape of Water", "Crimson Peak",
        "The Orphanage", "Cronos", "Hellboy", "Mama", "Pinocchio",
    ],
    "Inception": [
        "Tenet", "Interstellar", "The Matrix", "Memento", "Shutter Island",
        "Eternal Sunshine of the Spotless Mind", "The Prestige", "Source Code",
        "Predestination",
    ],
    "The Godfather": [
        "Goodfellas", "Casino", "The Departed", "Once Upon a Time in America",
        "Heat", "Scarface", "A Bronx Tale", "Donnie Brasco",
    ],
    "Goodfellas": [
        "The Godfather", "Casino", "The Departed",
        "Once Upon a Time in America", "Heat", "American Gangster",
        "Donnie Brasco",
    ],
    "Spirited Away": [
        "Howl's Moving Castle", "Castle in the Sky", "Princess Mononoke",
        "My Neighbor Totoro", "Kiki's Delivery Service", "Ponyo",
        "Mary and the Witch's Flower", "The Cat Returns",
    ],
}

_TOP_K = 20
_MIN_TOTAL_HITS = 10        # baseline 13; -3 drift tolerance.
_MIN_HIT_ANCHORS = 4         # at least 4 of 7 anchors must produce ≥1 hit.


async def _anchor_hits_in_top_k(anchor_title: str, expected_titles: list[str], top_k: int) -> int:
    """Resolve the anchor's tmdb_id from DB, fetch its stored vector, then
    query the live Qdrant index and count expected-neighbour hits in the
    top-K (excluding the anchor itself which always returns at rank 0)."""
    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            select(Movie.tmdb_id).where(
                or_(
                    Movie.title.ilike(anchor_title),
                    Movie.original_title.ilike(anchor_title),
                )
            ).limit(1)
        )).first()

    if row is None:
        return 0

    tmdb_id = row[0]
    qd = QdrantService()
    vector = await qd.get_vector(tmdb_id)
    if vector is None:
        return 0

    kwargs = {"collection_name": "movies", "query": vector, "limit": top_k + 1}
    if SearchParams is not None:
        kwargs["search_params"] = SearchParams(hnsw_ef=128)
    result = await qd.client.query_points(**kwargs)
    # Skip rank-0 (the anchor itself) and check the next top_k.
    expected_lower = {e.lower() for e in expected_titles}
    titles = [(h.payload.get("title") or "") for h in result.points[1:top_k + 1]]
    return sum(1 for t in titles if t.lower() in expected_lower)


@pytest.mark.asyncio
async def test_qdrant_recall_floor():
    """Total hits across all 7 anchors must clear the regression floor."""
    total = 0
    anchors_with_any_hit = 0
    per_anchor: dict[str, int] = {}

    for anchor, expected in _ANCHORS_AND_NEIGHBOURS.items():
        n = await _anchor_hits_in_top_k(anchor, expected, _TOP_K)
        per_anchor[anchor] = n
        total += n
        if n > 0:
            anchors_with_any_hit += 1

    # Print breakdown so a failing CI run shows where the regression hit.
    print(f"\n[golden-set] hits@{_TOP_K}: total={total}  anchors_with_hit={anchors_with_any_hit}/7")
    for anchor, n in per_anchor.items():
        print(f"  {anchor!r:32s} {n}/{len(_ANCHORS_AND_NEIGHBOURS[anchor])}")

    assert total >= _MIN_TOTAL_HITS, (
        f"Qdrant recall regression: total hits@{_TOP_K} = {total}, "
        f"min required = {_MIN_TOTAL_HITS}. Breakdown: {per_anchor}"
    )
    assert anchors_with_any_hit >= _MIN_HIT_ANCHORS, (
        f"Coverage regression: only {anchors_with_any_hit}/7 anchors produced any hit "
        f"(min {_MIN_HIT_ANCHORS}). One of the anchors fell off completely. "
        f"Breakdown: {per_anchor}"
    )
