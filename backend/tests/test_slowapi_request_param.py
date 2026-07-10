"""Regression net for the slowapi `request` param collision (2026-07-03).

slowapi's @limiter.limit wrapper requires the parameter NAMED `request` to be
the starlette Request. Six routes named their *pydantic body* `request` (with
the real one as `http_request`) → every call through the real route 500'd with
"parameter `request` must be an instance of starlette.requests.Request".
(Handler-level smoke tests missed it by calling `__wrapped__` directly.)

This test statically enforces the contract on EVERY registered route so the
whole bug class can't come back:
  - any param annotated `starlette.requests.Request` must be named `request`
  - no non-Request param may be named `request`

Hermetic: imports the app, inspects signatures. No HTTP, no DB, no Redis calls.
"""
import inspect
import os
import sys

sys.path.append(os.getcwd())

from starlette.requests import Request as StarletteRequest
from fastapi.routing import APIRoute


def _endpoint_signature(route):
    fn = route.endpoint
    # unwrap decorators (slowapi keeps the original under __wrapped__)
    seen = set()
    while hasattr(fn, "__wrapped__") and id(fn) not in seen:
        seen.add(id(fn))
        fn = fn.__wrapped__
    return inspect.signature(fn)


def test_every_route_names_the_starlette_request_param_request():
    from main import app

    offenders = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        sig = _endpoint_signature(route)
        for name, param in sig.parameters.items():
            ann = param.annotation
            if ann is StarletteRequest and name != "request":
                offenders.append(f"{route.path}: starlette Request param is named `{name}`")
            if ann is not StarletteRequest and name == "request":
                offenders.append(
                    f"{route.path}: param `request` is {getattr(ann, '__name__', ann)} — "
                    "slowapi will 500 on this route"
                )

    assert not offenders, "slowapi request-param contract violated:\n" + "\n".join(offenders)
