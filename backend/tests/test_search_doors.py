"""The two search doors — Fase 3 of the landing plan.

`/search/natural` was public while its own docstring said it must not be:
"Leaving it open to guests turns it into a paid-LLM proxy". Free text, no
session and a 200k-token daily budget is exactly that, and on 2026-07-25 the
budget was in fact exhausted (197,753 of 200,000).

These tests are the thing that stops it drifting open again. They assert the
CONTRACT — who may call what, and with what budget — by reading the route
signatures rather than issuing requests, so they stay hermetic: no Groq, no
Qdrant, no network.
"""
import inspect

import pytest
from fastapi import Depends

from routers import search as search_router


def _dependency_names(fn) -> set[str]:
    """The dependency callables a route resolves, by name."""
    out = set()
    for param in inspect.signature(fn).parameters.values():
        default = param.default
        if isinstance(default, type(Depends(lambda: None))):
            dep = getattr(default, "dependency", None)
            if dep is not None:
                out.add(dep.__name__)
    return out


def _route(path: str):
    for r in search_router.router.routes:
        if getattr(r, "path", None) == path:
            return r
    raise AssertionError(f"no route registered at {path}")


# ── who may knock ───────────────────────────────────────────────────────────
def test_natural_requires_a_real_session():
    """The whole point of Fase 3. If this fails, the LLM proxy is open again."""
    deps = _dependency_names(_route("/natural").endpoint)
    assert "get_current_user" in deps, "/natural must require auth"
    assert "get_optional_current_user" not in deps, (
        "/natural is back to optional auth — that is the exact drift Fase 3 fixed"
    )


def test_try_resolves_no_session_at_all():
    """/try must behave identically for everyone, so it reads no user."""
    deps = _dependency_names(_route("/try").endpoint)
    assert not {"get_current_user", "get_optional_current_user"} & deps, (
        "/try must not resolve a session: a signed-in caller and a guest have to "
        "get the same answer from the same URL"
    )


def test_showcase_reaches_neither_engine_nor_session():
    deps = _dependency_names(_route("/showcase").endpoint)
    assert "get_redis" in deps
    assert not {"get_current_user", "get_optional_current_user",
                "get_qdrant_service", "get_embedding_service"} & deps


# ── with what budget ────────────────────────────────────────────────────────
def test_guest_query_ceiling_is_far_below_the_authenticated_one():
    guest = search_router.TrySearchRequest.model_fields["query"]
    full = search_router.SearchRequest.model_fields["query"]
    guest_max = guest.annotation.__metadata__[0].max_length if hasattr(guest.annotation, "__metadata__") \
        else search_router.TRY_MAX_QUERY_LENGTH
    assert search_router.TRY_MAX_QUERY_LENGTH == 140
    assert guest_max <= 140 < 500, "the guest ceiling must stay well under the authenticated one"


@pytest.mark.parametrize("field", ["forced_intent", "use_deep_analysis"])
def test_try_refuses_the_expensive_and_the_privileged_fields(field):
    """Both are reachable on /natural and must not be on /try.

    `forced_intent` skips the parser and hands the caller direct control of the
    Qdrant filters; `use_deep_analysis` triggers the Tier-2 LLM. Neither belongs
    in an anonymous request.
    """
    assert field not in search_router.TrySearchRequest.model_fields
    assert field in search_router.SearchRequest.model_fields


def test_try_rejects_unknown_fields_instead_of_ignoring_them():
    """extra='forbid': an attempt to smuggle a field should 422, not 200."""
    with pytest.raises(Exception) as exc:
        search_router.TrySearchRequest(query="terror", forced_intent={"semantic_query": "x"})
    assert "Extra inputs are not permitted" in str(exc.value)


def test_try_is_rate_limited_harder_than_natural():
    limits = {}
    for path in ("/natural", "/try"):
        src = inspect.getsource(_route(path).endpoint)
        limits[path] = src  # decorator text is not on the function; read the module
    module = inspect.getsource(search_router)
    # the decorator sits directly above each def
    natural = module.split('@router.post("/natural"')[1].split("async def")[0]
    try_ = module.split('@router.post("/try"')[1].split("async def")[0]
    assert '"10/minute"' in natural
    assert '"5/minute"' in try_


def test_shared_body_is_not_itself_a_route():
    """_run_natural_search carries no auth of its own — the doors decide."""
    paths = {getattr(r, "path", None) for r in search_router.router.routes}
    assert "/_run_natural_search" not in paths
    deps = _dependency_names(search_router._run_natural_search)
    assert not deps, "the shared body must take plain arguments, not Depends()"
