"""`upsert_movie_vector` REPLACES the payload, so an omitted key is a deleted key.

The film stops matching any filter on that dimension — silently, no error, no log
line. CLAUDE.md has carried this warning for `has_enriched_embedding` since
2026-07-13 ("every payload writer must stamp it — a missing key makes the film
invisible in recs with NO error"), and on 2026-07-30 an audit found five writers
building the dict by hand and dropping the five dimensions added the day before.
Among them `get_or_create_movie`: every newly ingested film would have arrived
with no country, so no country query could ever reach it.

A warning nobody can run is a warning that expires. This is the runnable form:
one canonical builder, and no call site allowed to hand-roll a dict.

Third sibling of test_qdrant_filter_contract and test_no_dead_parameters — same
class (something declared, silently not applied), different surface.
"""
import ast
from pathlib import Path

from models.database import Movie
from models.external_schemas import QdrantPayload, qdrant_payload

BACKEND = Path(__file__).resolve().parent.parent
# Dimensions Qdrant filters on. Losing any of them from a point removes that film
# from every search that constrains it.
FILTERED = [
    "tmdb_id", "genres", "year", "runtime", "original_language", "vectorbox_score",
    "vote_count", "vote_average", "imdb_rating", "metacritic_rating",
    "has_enriched_embedding", "countries", "spoken_languages", "mpaa_rating",
    "oscar_wins", "is_adult",
    # 2026-08-19: no estaban, y por eso nada gritó cuando cada re-upsert los
    # borraba. Phase 7 curó un punto y salió sin ejes de mood; 7 puntos tenían
    # el valor en Postgres y no en el payload.
    "mood_gravedad", "mood_humanidad",
]


def _fake_movie():
    m = Movie()
    for f in ("tmdb_id", "year", "runtime", "vote_count", "oscar_wins"):
        setattr(m, f, 1)
    for f in ("title", "overview", "original_language", "poster_path", "title_es",
              "overview_es", "mpaa_rating"):
        setattr(m, f, "x")
    for f in ("genres", "keywords", "directors", "cast", "omdb_countries", "omdb_languages"):
        setattr(m, f, [])
    for f in ("vote_average", "vectorbox_score", "imdb_rating", "metacritic_rating",
              "mood_gravedad", "mood_humanidad"):
        setattr(m, f, 1.0)
    m.has_enriched_embedding = True
    m.is_adult = False
    return m


def test_the_canonical_payload_carries_every_filtered_dimension():
    p = qdrant_payload(_fake_movie())
    missing = [f for f in FILTERED if f not in p]
    assert not missing, f"qdrant_payload drops {missing} — those films stop matching those filters"


def test_the_schema_and_the_builder_agree():
    """QdrantPayload is the typed contract; drift between the two is how a field
    gets declared in one place and never written in the other."""
    built = set(qdrant_payload(_fake_movie()))
    declared = set(QdrantPayload.model_fields)
    # `rating` is the schema's name for vote_average; the builder writes the
    # payload key Qdrant actually stores.
    assert declared - built - {"rating"} == set(), f"declared but never written: {declared - built - {'rating'}}"


def test_enriched_override_reaches_the_payload():
    """Callers that upsert BEFORE flipping the row flag depend on this; getting it
    wrong excludes the film from every gated recommendation surface."""
    assert qdrant_payload(_fake_movie(), enriched=False)["has_enriched_embedding"] is False


def test_overrides_win_over_the_row():
    """enrich_vectors holds keywords fresher than the row it upserts from."""
    assert qdrant_payload(_fake_movie(), keywords=["fresh"])["keywords"] == ["fresh"]


def test_no_call_site_hand_rolls_a_payload():
    """Every upsert_movie_vector call must pass a payload built by the canonical
    function — not a dict literal assembled next to it."""
    offenders = []
    for path in sorted(BACKEND.rglob("*.py")):
        if "test" in path.name or "__pycache__" in str(path):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "upsert_movie_vector"):
                continue
            meta = next((k.value for k in node.keywords if k.arg == "metadata"), None)
            if isinstance(meta, ast.Dict):
                offenders.append(f"{path.name}:{node.lineno} builds a dict literal inline")
            elif isinstance(meta, ast.Name):
                # a variable — check it was assigned from the builder somewhere above
                src = path.read_text(encoding="utf-8", errors="replace")
                if f"{meta.id} = qdrant_payload(" not in src and f"{meta.id} = _qdrant_payload(" not in src:
                    offenders.append(f"{path.name}:{node.lineno} passes `{meta.id}`, not built by qdrant_payload()")

    assert not offenders, (
        "These writers can silently delete payload keys:\n  " + "\n  ".join(offenders)
        + "\nUse models.external_schemas.qdrant_payload(movie) — pass keyword "
          "overrides for values you hold fresher than the row."
    )
