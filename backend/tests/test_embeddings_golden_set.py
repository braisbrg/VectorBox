"""TST-2 — golden-set THEMATIC-COHERENCE regression for the production Qdrant index.

Re-baselined 2026-06-27. The production embedding (V2 `cinematic_description`,
name-free) clusters by **theme / mood / movement / style** — identity tokens
(director, studio, franchise) are deliberately name-banned and served by
*structured signals* instead (collection row, director-centroid). So this test
guards the embedding's *actual* job: thematic coherence. Franchise recall is NOT
the embedding's responsibility and lives in separate structured-signal tests.

Why the change: the previous version asserted recall of hand-curated
*franchise/auteur* neighbours (Ghibli↔Ghibli, Scorsese↔Scorsese). That set had
**survivorship bias** — every anchor was a famous franchise film, so it rewarded
any recipe that injected director/studio identity and could not see the downside.
A fair-set eval (2026-06-27) proved the pure embedding does the *right* thing on
adversarial anchors (The Shining → psych-horror, not Kubrick's sci-fi; Stalker →
cross-director slow cinema), so the recipe was kept pure and this test reframed.

Metric (per anchor, top-10 neighbours):
  - `genre_frac`  — fraction of neighbours sharing ≥1 genre with the anchor.
  - `distinct_directors` — how many distinct directors appear in the top-10.
A healthy theme-embedding scores high genre_frac AND high director diversity
(it groups by theme across many directors). A broken model/recipe, a half-empty
catalog, or an accidental identity-collapse (e.g. director re-added to the
embedding) drops one or both.

Anchors are a DIVERSE, bias-free set: versatile directors, cross-director
movements, genre-commodity, franchise, standalone.
"""
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

# Needs a populated Qdrant + Postgres (the live catalogue). Flagged so the
# hermetic suite can exclude it with `pytest -m "not integration"`.
pytestmark = pytest.mark.integration

# Diverse, bias-free anchors — NOT all franchise films (that was the old bias).
_ANCHORS = [
    "The Shining",          # versatile director (Kubrick) — must stay psych-horror
    "Barry Lyndon",         # versatile director — must stay period drama
    "Stalker",              # cross-director movement (slow/contemplative sci-fi)
    "The Conjuring",        # genre-commodity horror
    "Parasite",             # standalone — class satire/thriller
    "Whiplash",             # standalone — obsessive-performance drama
    "Pan's Labyrinth",      # auteur dark fantasy
    "Inception",            # high-concept mind-bender
]

_TOP_K = 10
# Floors are set below observed healthy values (calibrated 2026-06-27) so they
# catch a real regression without flapping on single-film churn.
_MIN_MEAN_GENRE_FRAC = 0.70     # observed ~0.90+
_MIN_ANCHOR_GENRE_FRAC = 0.40   # weakest healthy anchor stays well above
_MIN_MEAN_DISTINCT_DIRECTORS = 6.0  # observed ~20 — guards against identity-collapse


async def _anchor_metrics(anchor_title: str) -> tuple[float, int] | None:
    """Return (genre_frac, distinct_directors) for the anchor's top-K neighbours."""
    async with AsyncSessionLocal() as db:
        anchor = (await db.execute(
            select(Movie).where(
                or_(Movie.title.ilike(anchor_title), Movie.original_title.ilike(anchor_title))
            ).limit(1)
        )).scalar_one_or_none()
        if anchor is None:
            return None

        qd = QdrantService()
        vector = await qd.get_vector(anchor.tmdb_id)
        if vector is None:
            return None

        kwargs = {"collection_name": "movies", "query": vector, "limit": _TOP_K + 1}
        if SearchParams is not None:
            kwargs["search_params"] = SearchParams(hnsw_ef=128)
        result = await qd.client.query_points(**kwargs)
        hit_ids = [p.id for p in result.points[1:_TOP_K + 1]]

        rows = (await db.execute(select(Movie).where(Movie.tmdb_id.in_(hit_ids)))).scalars().all()

    a_genres = set(anchor.genres or [])
    if not a_genres or not rows:
        return (0.0, 0)
    shares = sum(1 for m in rows if a_genres & set(m.genres or []))
    distinct_dirs = {d for m in rows for d in (m.directors or [])}
    return (shares / len(rows), len(distinct_dirs))


@pytest.mark.asyncio
async def test_embedding_thematic_coherence():
    """Top-K neighbours must be thematically coherent AND director-diverse."""
    genre_fracs: dict[str, float] = {}
    director_counts: dict[str, int] = {}

    for anchor in _ANCHORS:
        m = await _anchor_metrics(anchor)
        if m is None:
            continue  # anchor not in catalog — skip rather than fail
        genre_fracs[anchor], director_counts[anchor] = m

    assert genre_fracs, "no anchors resolved — catalog/Qdrant not populated?"

    mean_genre = sum(genre_fracs.values()) / len(genre_fracs)
    mean_dirs = sum(director_counts.values()) / len(director_counts)

    print(f"\n[golden-set] thematic coherence over {len(genre_fracs)} anchors")
    print(f"  mean genre_frac={mean_genre:.2f}  mean distinct_directors={mean_dirs:.1f}")
    for a in genre_fracs:
        print(f"  {a!r:24s} genre_frac={genre_fracs[a]:.2f}  distinct_dirs={director_counts[a]}")

    weakest = min(genre_fracs.values())
    assert mean_genre >= _MIN_MEAN_GENRE_FRAC, (
        f"Thematic-coherence regression: mean genre_frac={mean_genre:.2f} "
        f"< {_MIN_MEAN_GENRE_FRAC}. Breakdown: {genre_fracs}"
    )
    assert weakest >= _MIN_ANCHOR_GENRE_FRAC, (
        f"An anchor lost thematic coherence: min genre_frac={weakest:.2f} "
        f"< {_MIN_ANCHOR_GENRE_FRAC}. Breakdown: {genre_fracs}"
    )
    assert mean_dirs >= _MIN_MEAN_DISTINCT_DIRECTORS, (
        f"Director-diversity collapse (identity leaked into the embedding?): "
        f"mean distinct_directors={mean_dirs:.1f} < {_MIN_MEAN_DISTINCT_DIRECTORS}. "
        f"Breakdown: {director_counts}"
    )
