"""A filter key nobody handles is a constraint that never happened.

The search still succeeds, the results still look plausible, and the only
evidence is that the answer is subtly wrong. Three of these were live until
2026-07-29 — routers/search.py passed `mpaa_ratings`, `min_oscar_wins` and
`exclude_adult` to a `search_similar` that had never heard of them — and a
fourth was found the same day one layer up, where the feed handed the rail's
constraints to the dict meant for provider ids.

None of them failed a test, because there was nothing to fail. This is that
test: it diffs every filter key written anywhere in the codebase against the set
`search_similar` acts on, statically, with no services running.
"""
import re
from pathlib import Path

import pytest

from services.qdrant_service import QdrantService

BACKEND = Path(__file__).resolve().parent.parent

# Keys assigned into a dict that is destined for search_similar.
_WRITE = re.compile(r'(?:qdrant_filters|search_filters)\[\s*["\']([a-z_]+)["\']\s*\]\s*=')
# Inline literals: filters={"key": ...}
_INLINE = re.compile(r'filters\s*=\s*\{\s*["\']([a-z_]+)["\']')


def _sources():
    for path in list(BACKEND.glob("routers/*.py")) + list(BACKEND.glob("services/*.py")):
        yield path, path.read_text(encoding="utf-8", errors="replace")


def test_every_filter_key_written_is_a_key_search_similar_acts_on():
    ghosts = {}
    for path, text in _sources():
        for rx in (_WRITE, _INLINE):
            for m in rx.finditer(text):
                key = m.group(1)
                if key not in QdrantService.FILTER_KEYS:
                    ghosts.setdefault(key, set()).add(path.name)

    assert not ghosts, (
        "These keys are written but search_similar ignores them, so the search "
        "runs WITHOUT the constraint and nothing errors:\n"
        + "\n".join(f"  {k}  <- {', '.join(sorted(v))}" for k, v in sorted(ghosts.items()))
        + "\nEither handle the key in search_similar and add it to FILTER_KEYS, "
          "or stop passing it."
    )


def test_filter_keys_is_not_a_rubber_stamp():
    """FILTER_KEYS must list keys the method really branches on — otherwise the
    test above passes by widening the set instead of fixing the caller."""
    src = (BACKEND / "services" / "qdrant_service.py").read_text(encoding="utf-8")
    body = src.split("async def search_similar", 1)[1]
    consulted = set(re.findall(r'["\']([a-z_]+)["\']\s+in\s+filters', body))
    consulted |= set(re.findall(r'filters\.get\(\s*["\']([a-z_]+)["\']', body))

    unbranched = QdrantService.FILTER_KEYS - consulted
    assert not unbranched, (
        f"declared in FILTER_KEYS but never branched on: {sorted(unbranched)}"
    )


@pytest.mark.parametrize("key", [
    "countries", "spoken_languages", "mpaa_ratings", "min_oscar_wins",
    "exclude_adult", "min_vectorbox_score",
])
def test_the_dimensions_that_moved_into_the_payload_are_declared(key):
    """Pins the 2026-07-29 migration: these were post-filtered in Postgres, which
    starved them — "thrillers coreanos" kept 1 Korean film of 219 in twenty
    candidates, and 20 of 20 once the country filter reached Qdrant."""
    assert key in QdrantService.FILTER_KEYS
