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
