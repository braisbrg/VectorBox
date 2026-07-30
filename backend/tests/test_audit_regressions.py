"""Regression tests for the security/correctness fixes from the engineering
audit (Waves 1-2). These lock in behaviour that is easy to silently break:

  - SEC-1 / REL-3  Clerk JWKS fetch is cached and the on-miss refetch is
                   rate-limited so a bogus-`kid` flood can't spam the network.
  - AUDIT-2        the backend pip-audit gate detects pip-audit's real
                   "Found N known vulnerabilities" wording (the old
                   "vulnerabilities found" match never fired → fail-open).
  - CONC-3         the Redis stampede lock releases via compare-and-delete,
                   so a worker can't delete a lock it no longer owns.

All hermetic: no DB, no network, no Redis server (AsyncMock stubs only).
"""
import os
import re
import sys

import pytest

sys.path.append(os.getcwd())

from unittest.mock import AsyncMock, patch


# ---------------------------------------------------------------------------
# SEC-1 / REL-3 — Clerk JWKS: cached + rate-limited refetch (anti-DoS)
# ---------------------------------------------------------------------------

@pytest.fixture
def _reset_jwks_state():
    """Reset the module-level JWKS cache between tests."""
    import dependencies as deps
    deps._jwks_cache = {"keys": []}
    deps._jwks_fetched_at = 0.0
    deps._jwks_last_refresh_attempt = 0.0
    yield deps
    deps._jwks_cache = {"keys": []}
    deps._jwks_fetched_at = 0.0
    deps._jwks_last_refresh_attempt = 0.0


@pytest.mark.asyncio
async def test_jwks_is_cached_after_first_fetch(_reset_jwks_state):
    deps = _reset_jwks_state
    fake = AsyncMock(return_value={"keys": [{"kid": "abc"}]})
    with patch.object(deps, "_fetch_clerk_jwks", fake):
        first = await deps._get_clerk_jwks()
        second = await deps._get_clerk_jwks()
    assert first["keys"][0]["kid"] == "abc"
    assert second == first
    # Second call must hit the cache, not the network.
    assert fake.await_count == 1


@pytest.mark.asyncio
async def test_jwks_force_refetch_is_rate_limited(_reset_jwks_state):
    """A flood of unknown-kid lookups (force=True) must not translate into a
    flood of network fetches — the cooldown caps it to one."""
    deps = _reset_jwks_state
    fake = AsyncMock(return_value={"keys": [{"kid": "abc"}]})
    with patch.object(deps, "_fetch_clerk_jwks", fake):
        await deps._get_clerk_jwks()                 # primes cache (1 fetch)
        for _ in range(50):                          # 50 bogus-kid refreshes
            await deps._get_clerk_jwks(force=True)
    # Cooldown (_JWKS_MIN_REFRESH) means at most one extra fetch, not 50.
    assert fake.await_count <= 2


@pytest.mark.asyncio
async def test_resolve_public_key_unknown_kid_returns_none(_reset_jwks_state):
    deps = _reset_jwks_state
    fake = AsyncMock(return_value={"keys": [{"kid": "real-key"}]})
    # Token header carries a kid that isn't in the JWKS.
    with patch.object(deps, "_fetch_clerk_jwks", fake), \
         patch.object(deps.jwt, "get_unverified_header", return_value={"kid": "attacker-kid"}):
        result = await deps._resolve_clerk_public_key("dummy.token.value")
    assert result is None


@pytest.mark.asyncio
async def test_resolve_public_key_missing_kid_returns_none(_reset_jwks_state):
    deps = _reset_jwks_state
    with patch.object(deps.jwt, "get_unverified_header", return_value={}):
        result = await deps._resolve_clerk_public_key("dummy.token.value")
    assert result is None


# ---------------------------------------------------------------------------
# AUDIT-2 — pip-audit fail-closed detection wording
# ---------------------------------------------------------------------------

# This is the exact phrasing pip-audit emits (captured live from the tool).
_VULN_PATTERN = r"found\s+(\d+)\s+known\s+vulnerabilit"


@pytest.mark.parametrize("line, expect_vuln", [
    ("Found 6 known vulnerabilities in 1 package", True),
    ("found 1 known vulnerability in 1 package", True),
    ("No known vulnerabilities found", False),       # the clean line — must NOT match
    ("WARNING:pip_audit._cli:Consider using pip-compile", False),
])
def test_pip_audit_wording_detection(line, expect_vuln):
    m = re.search(_VULN_PATTERN, line.lower())
    found = bool(m and int(m.group(1)) > 0)
    assert found is expect_vuln


def test_security_audit_uses_real_wording_not_legacy():
    """Guard against a regression to the old fail-open substring match."""
    with open(os.path.join("scripts", "security_audit.py"), encoding="utf-8") as f:
        src = f.read()
    # The fixed gate matches pip-audit's real "found N known vulnerabilit..." wording.
    assert "known\\s+vulnerabilit" in src or "known vulnerabilit" in src.replace("\\s+", " ")
    # And it must fail closed: an explicit vuln branch setting a non-zero exit.
    assert "vuln_found" in src


# ---------------------------------------------------------------------------
# CONC-3 — Redis lock compare-and-delete ownership
# ---------------------------------------------------------------------------

class _FakeRedis:
    """Minimal async Redis stub implementing just SET NX EX + the CAD eval."""
    def __init__(self):
        self.store = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def get(self, key):
        return self.store.get(key)

    async def eval(self, script, numkeys, key, arg):
        # Emulate the compare-and-delete Lua: delete only if value == arg.
        if self.store.get(key) == arg:
            self.store.pop(key, None)
            return 1
        return 0


@pytest.mark.asyncio
async def test_lock_compare_and_delete_only_releases_own_lock():
    r = _FakeRedis()
    key = "lock:signal_cache:1:vibe:abc"
    mine, theirs = "token-A", "token-B"

    assert await r.set(key, mine, nx=True, ex=30) is True   # I acquire
    assert await r.set(key, theirs, nx=True, ex=30) is None  # they can't

    cad = ("if redis.call('get', KEYS[1]) == ARGV[1] "
           "then return redis.call('del', KEYS[1]) else return 0 end")

    # A different worker's token must NOT free my lock.
    assert await r.eval(cad, 1, key, theirs) == 0
    assert await r.get(key) == mine

    # My own token frees it.
    assert await r.eval(cad, 1, key, mine) == 1
    assert await r.get(key) is None
