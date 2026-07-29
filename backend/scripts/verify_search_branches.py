"""Which branch answers, verified WITHOUT the parser — 2026-07-29.

`audit_search.py` measures the whole product, parser included, and that is what
it is for. It is also why its aggregate score cannot be trusted: Groq's free tier
saturates at 8000 tokens per MINUTE, a parse costs ~2000, and a 41-query run
tips it repeatedly. Rows that never got parsed are excluded from the score, but
enough of them and the score is measuring a handful of lucky queries.

So the engine gets its own check. `SearchRequest.forced_intent` bypasses the LLM
entirely, which makes every decision downstream of parsing deterministic and
repeatable: the same intent must always reach the same branch and return films
that clear the same bar. Nothing here calls Groq, so it can run on any budget,
as many times as you like.

What it does NOT cover: whether the parser produces these intents from real
sentences. That is audit_search.py's job, and it needs the parser to work.

    docker compose exec backend python scripts/verify_search_branches.py
    docker compose exec backend python scripts/verify_search_branches.py --repeat 5
"""
import argparse
import asyncio
import logging
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import AsyncSessionLocal
from routers.search import (
    AUDIENCE_SELECTION_REASONING,
    CATALOGUE_SELECTION_REASONING,
    SearchRequest,
    _run_natural_search,
)
from services.embedding_service import EmbeddingService
from services.nlp_search import MovieSearchIntent
from services.qdrant_service import QdrantService
from services.tmdb_client import TMDBClient

logging.getLogger().setLevel(logging.ERROR)

# (label, query, intent kwargs, expected branch, min films, min mean VBS of top 5)
#
# The intents are the ones the parser was OBSERVED to produce for these sentences,
# so this pins the engine's half of the contract while audit_search.py pins the
# parser's. min_vbs is None where the branch returns a row shape without VBS.
CASES = [
    ("tema puro", "algo lento y triste sobre el duelo, sin sustos",
     dict(semantic_query="grief, mourning, loss, quiet sorrow, contemplative"),
     "vector", 5, 60),

    ("tema + genero", "comedias de los 80",
     dict(semantic_query="comedy, humour, funny", include_genres=["Comedy"],
          year_min=1980, year_max=1989),
     "vector", 5, 60),

    # KNOWN CEILING, not a pass mark. `countries` is enforced in Postgres and
    # nothing here can be pushed into Qdrant, so the wide fetch is all we have —
    # and measured in isolation it buys 1 -> 3 survivors, no more. 150 nearest
    # neighbours of a generic "thriller, suspense, mystery" vector simply contain
    # about three Korean films, out of 219 in the catalogue.
    #
    # The live 1 -> 20 improvement recorded in commit 5a43f3c came from the
    # parser ALSO setting original_language="ko" on that run — a field Qdrant CAN
    # filter during the search — not from the wide fetch. Parser variance, and
    # the commit message overstated it.
    #
    # Contrast the heist row below, where year_min/max DO reach Qdrant: the same
    # wide fetch takes it from 7 survivors to 47.
    #
    # The real fix is indexing country in the Qdrant payload, which the code
    # comment in routers/search.py has called a follow-up since Sprint 1.
    ("post-filtro pais", "thrillers coreanos",
     dict(semantic_query="thriller, suspense, mystery, crime, tension",
          countries=["South Korea"]),
     "vector", 3, 50),

    ("post-filtro pais+anyo", "atracos con mucho estilo, cine europeo de los 70",
     dict(semantic_query="heist, stylish, caper, crime, slick robbery",
          countries=["France", "Germany", "Italy", "Spain", "United Kingdom"],
          year_min=1970, year_max=1979),
     "vector", 6, 65),

    # A bar and no subject. Which quality FIELD the parser reaches for used to
    # decide whether the user got twelve films or three.
    ("calidad sola (VBS)", "peliculas muy bien valoradas",
     dict(semantic_query="muy bien valoradas", min_vectorbox_score=75),
     "catalogue", 8, 75),
    ("calidad sola (metacritic)", "algo muy aclamado por la critica",
     dict(semantic_query="muy aclamado por la critica", min_metacritic=75),
     "catalogue", 8, 75),

    ("peticion abierta", "no se que ver",
     dict(semantic_query="no se que ver", open_request=True),
     "catalogue", 8, 75),

    # Audience: the vector is confidently wrong here (0.548, higher than one of
    # the best queries), so the branch must not depend on confidence at all.
    ("audiencia con genero", "una peli familiar para ver con niños",
     dict(semantic_query="family friendly, gentle, wholesome",
          audience_request=True, include_genres=["Family", "Animation"]),
     "audience", 8, 75),

    # Flagged as audience but with no genre to select on. It must NOT get the
    # audience branch — that would dress the catalogue's top scorers as a family
    # selection — but it must not be refused either: someone describing the room
    # is still asking for a suggestion. Falls to the vector when the theme has
    # signal, to the catalogue when it does not. Never empty.
    ("audiencia sin genero", "algo que terminemos mis padres y yo sin discutir",
     dict(semantic_query="something we can all agree on", audience_request=True),
     "catalogue", 8, 75),

    ("sinsentido", "asdkjh qwe zxcvbn",
     dict(semantic_query="asdkjh qwe zxcvbn"),
     "refuse", 0, None),
    ("fuera de dominio", "cual es la capital de Francia",
     dict(semantic_query="capital of France geography question"),
     "refuse", 0, None),
]


def branch_of(resp) -> str:
    if resp.low_confidence:
        return "refuse"
    reasoning = (resp.intent or {}).get("reasoning") or ""
    if reasoning == CATALOGUE_SELECTION_REASONING:
        return "catalogue"
    if reasoning == AUDIENCE_SELECTION_REASONING:
        return "audience"
    return "vector"


async def run_case(query, kwargs, tmdb, qdrant, emb) -> tuple:
    intent = MovieSearchIntent(reasoning="forced (verify_search_branches)", **kwargs)
    async with AsyncSessionLocal() as db:
        resp = await _run_natural_search(
            SearchRequest(query=query, forced_intent=intent),
            None, db, tmdb, qdrant, emb,
        )
    rows = resp.results or []
    vbs = [r["vectorbox_score"] for r in rows[:5] if r.get("vectorbox_score")]
    return branch_of(resp), len(rows), (statistics.fmean(vbs) if vbs else 0.0), resp.degraded


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repeat", type=int, default=3,
                    help="pasadas por caso; varias ramas barajan, así que >1 prueba estabilidad")
    args = ap.parse_args()

    tmdb, qdrant, emb = TMDBClient(), QdrantService(), EmbeddingService()
    failures = []
    print(f"{len(CASES)} casos × {args.repeat} pasada(s) — sin LLM\n")
    print(f"  {'':2s} {'caso':<26} {'rama':<10} {'n':>4} {'VBS':>5}")

    for label, query, kwargs, want_branch, min_n, min_vbs in CASES:
        branches, counts, scores = set(), [], []
        for _ in range(args.repeat):
            b, n, v, degraded = await run_case(query, kwargs, tmdb, qdrant, emb)
            if degraded:  # forced_intent must never look like a failed parse
                failures.append(f"{label}: degraded=True con forced_intent")
            branches.add(b)
            counts.append(n)
            scores.append(v)

        bad = []
        if branches != {want_branch}:
            bad.append(f"rama={'/'.join(sorted(branches))} (esperada {want_branch})")
        if want_branch == "refuse":
            if max(counts) > 0:
                bad.append(f"devolvio {max(counts)}")
        else:
            if min(counts) < min_n:
                bad.append(f"n={min(counts)}<{min_n}")
            if min_vbs is not None and statistics.fmean(scores) < min_vbs:
                bad.append(f"VBS {statistics.fmean(scores):.0f}<{min_vbs}")

        mark = "ok" if not bad else "XX"
        print(f"  {mark} {label:<26} {'/'.join(sorted(branches)):<10} "
              f"{min(counts):>2}-{max(counts):<2} {statistics.fmean(scores):>5.0f}"
              f"{'   <- ' + ', '.join(bad) if bad else ''}")
        failures += [f"{label}: {b}" for b in bad]

    print("\n" + "=" * 70)
    print(f"  {len(CASES) - len({f.split(':')[0] for f in failures})}/{len(CASES)} casos correctos")
    if failures:
        print("\n  Fallos:")
        for f in failures:
            print(f"    {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
