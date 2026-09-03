"""Static scan of the backend source for the invariants documented in CLAUDE.md.

These rules used to live only as prose repeated across CLAUDE.md, AGENTS.md and
STACK_RULES.md. A rule that is only written down gets violated silently
(`reset_profiles.py` was calling `KEYS *` for months); a rule that is executable
fails the suite. Prose is the explanation, this file is the enforcement.

Adding a rule: append to RULES. `pattern` is searched line by line over every
`.py` under backend/ except EXCLUDED_DIRS. Keep patterns zero-false-positive —
a check that cries wolf gets deleted, and then the rule is unenforced again.
"""
import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
EXCLUDED_DIRS = {"tests", ".venv", "__pycache__", "alembic", "migrations"}

RULES = [
    (
        "sqlalchemy-boolean-comparison",
        r"[=!]=\s*(True|False)\b",
        "Use .is_(True) / .is_(False) — `== True` breaks index usage and NULL semantics.",
    ),
    (
        "redis-from-url-is-sync",
        r"await\s+[\w\.]*from_url\s*\(",
        "aioredis.from_url() is SYNC in redis-py >= 4.2 — never await it.",
    ),
    (
        "redis-keys-banned",
        r"await\s+[\w\.]+\.keys\s*\(",
        "Enumerate Redis keys with SCAN (services.cache_service.scan_and_delete), never KEYS — KEYS is O(N) and blocks the event loop.",
    ),
    (
        "qdrant-id-confusion",
        r"tmdb_id\s*=\s*\w+\.id\b",
        "Qdrant point IDs are Movie.tmdb_id, never Movie.id — passing the PG id makes the film invisible with no error.",
    ),
]

# ponytail: line-level regex, not an AST pass. It cannot see a rule violated
# across two lines, and it reads its own `pattern` strings as source. Upgrade to
# ast.NodeVisitor only if a real violation ever slips through this.
SELF = Path(__file__).name


def _source_files():
    return [
        p for p in BACKEND.rglob("*.py")
        if not (EXCLUDED_DIRS & set(p.relative_to(BACKEND).parts)) and p.name != SELF
    ]


@pytest.mark.parametrize("name,pattern,why", RULES, ids=[r[0] for r in RULES])
def test_invariant_not_violated(name, pattern, why):
    rx = re.compile(pattern)
    hits = [
        f"{p.relative_to(BACKEND)}:{n}: {line.strip()}"
        for p in _source_files()
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if rx.search(line)
    ]
    assert not hits, f"{why}\n" + "\n".join(hits)


def test_scan_finds_the_source_tree():
    """Guard the guard: a broken path glob would make every rule above pass vacuously."""
    files = _source_files()
    assert len(files) > 50, f"only {len(files)} source files scanned — check BACKEND/EXCLUDED_DIRS"


def test_title_lookups_go_through_the_ordered_picker():
    """`.first()` on an unordered SELECT is a coin flip, not a lookup.

    811 catalogue titles are shared by two or more films, so both title lookups
    in search.py returned an arbitrary one: "Mother" could be Bong Joon-ho (VBS
    86) or Pudovkin 1926, "The Hunt" Vinterberg (91) or Blumhouse (54). Named
    after the report that found it — "james bond" answered "Movies like Being
    James Bond", a Daniel Craig documentary, because that was simply the row
    Postgres yielded first.

    Enforced structurally rather than by regexing for `.first()`: the codebase
    has many legitimate ones, and a rule that cries wolf gets deleted.
    """
    src = (BACKEND / "routers" / "search.py").read_text(encoding="utf-8")
    start = src.index("async def _pick_reference_movie")
    end = src.index("async def _run_natural_search")
    picker, rest = src[start:end], src[:start] + src[end:]

    assert "order_by(" in picker, "the picker must impose an order"
    assert "nulls_last(" in picker, (
        "a NULL vectorbox_score must lose — 'Untitled …' placeholder rows carry "
        "is_upcoming=False, so nothing else keeps them out"
    )
    assert "Movie.title.ilike" not in rest, (
        "title lookups belong in _pick_reference_movie; a bare one is unordered "
        "again and picks whichever duplicate Postgres yields first"
    )


def test_point_vectors_are_never_read_raw():
    """`point.vector` es un DICT desde que la colección tiene vectores con nombre.

    La migración sparse (2026-08-03) rompió tres lectores a la vez —
    `RSSService._fetch_vectors`, el mapa de candidatos de group-sync y
    `ClusteringService`— porque los tres hacían `np.array(p.vector)` y ahora eso
    recibe `{'': [...], 'lexical': ...}`. Reventaban con "unsupported operand
    type(s) for +: 'dict' and 'dict'": group sync y el clustering caídos, y ni un
    test rojo, porque ninguno de los dos se ejecuta en la suite.

    `QdrantService._dense_of` ya existía cuando ocurrió — se había añadido para
    los lectores de la propia clase y no se aplicó al resto. Eso es lo que este
    test vigila: no que el helper exista, sino que nadie lea el vector sin él.
    """
    offenders = []
    for path in BACKEND.rglob("*.py"):
        if set(path.parts) & EXCLUDED_DIRS:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"np\.array\(\s*\w+\.vector\s*\)", line):
                offenders.append(f"{path.relative_to(BACKEND)}:{n}")
    assert not offenders, (
        "leen el vector crudo en vez de QdrantService._dense_of: " + ", ".join(offenders)
    )
