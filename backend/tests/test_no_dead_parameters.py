"""A parameter nobody reads is a promise the code does not keep.

`create_feed_item(include_rating=...)` was passed as True from nine call sites
and read by none: `rating=movie.vote_average` is set unconditionally, so the flag
looked like a switch and switched nothing. Nothing was broken — that is the point.
The next person to write `include_rating=False` expecting to hide ratings would
have found out the hard way.

Sibling of test_qdrant_filter_contract: same defect class (input accepted, never
acted on), different surface. Static, no services needed.

A parameter is allowed to go unread only when something OTHER than the body
consumes it:
  · a `Depends(...)` default — FastAPI runs the dependency, and that side effect
    is frequently the whole point (`verify_user_ownership` authorises the caller
    without the handler ever touching `current_user`).
  · `request` / `self` / `cls` — slowapi requires the first by name, Python the
    rest.
  · `**kwargs` passthroughs.
"""
import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
ALWAYS_OK = {"self", "cls", "request", "args", "kwargs"}


def _has_depends(default) -> bool:
    """True for `x = Depends(...)` — the dependency runs regardless of the body."""
    return (
        isinstance(default, ast.Call)
        and isinstance(default.func, ast.Name)
        and default.func.id == "Depends"
    )


def _dead_params(fn: ast.AST) -> list[str]:
    args = fn.args
    defaults = dict(zip([a.arg for a in args.args][-len(args.defaults):] if args.defaults else [],
                        args.defaults))
    defaults.update({a.arg: d for a, d in zip(args.kwonlyargs, args.kw_defaults) if d is not None})

    read = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    read |= {n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)}

    dead = []
    for a in args.args + args.kwonlyargs:
        if a.arg in ALWAYS_OK or a.arg in read:
            continue
        if _has_depends(defaults.get(a.arg)):
            continue
        dead.append(a.arg)
    return dead


def test_no_function_accepts_a_parameter_it_never_reads():
    offenders = []
    for path in sorted(list(BACKEND.glob("routers/*.py")) + list(BACKEND.glob("services/*.py"))):
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for name in _dead_params(fn):
                    offenders.append(f"{path.name}:{fn.lineno} {fn.name}() -> {name}")

    assert not offenders, (
        "These parameters are accepted and never read, so every caller passing "
        "them is being ignored in silence:\n  " + "\n  ".join(offenders)
        + "\nEither use the parameter or delete it (and its arguments). If "
          "something outside the body consumes it, give it a Depends(...) default."
    )
