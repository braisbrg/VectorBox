"""What the score MEANS on each surface, and whether each one pads — 2026-07-31.

`audit_search.py` measures the product end to end, parser included, and needs
Groq. This one asks a narrower question the parser cannot affect, so it runs on
any budget: given the same catalogue, what number does each surface put in front
of the user, and does the row stop when the answers run out?

Three formulas produce that number today:

    Magic Box        normalize_similarity_score(cos) * quality_gate_weight(VBS)
    Mas como esto    normalize_similarity_score(cos)          (routers/search.py)
    Feed / Trident   normalize_similarity_score(cos)          (recommendation_engine)

`normalize_similarity_score` floors at 60, so two of the three can never show a
number below 60 however bad the match; Magic Box multiplies by a weight whose
floor is 0.20, so it reaches 12. The same similarity is therefore a "60" on one
screen and a "24" on another, and neither is a percentage of anything.

    docker compose exec backend python scripts/audit_score_surfaces.py
    docker compose exec backend python scripts/audit_score_surfaces.py --panel magic
"""
import argparse
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import AsyncSessionLocal
from routers.search import (
    SearchRequest,
    _item_to_item_search,
    _pick_reference_movie,
    _run_natural_search,
)
from services.embedding_service import EmbeddingService
from services.magic_search_ranking import SEARCH_RESULT_LIMIT
from services.nlp_search import MovieSearchIntent
from services.qdrant_service import QdrantService
from services.tmdb_client import TMDBClient

logging.getLogger().setLevel(logging.ERROR)

# (etiqueta, forma, kwargs de intent). La forma agrupa el informe; los intents
# son de los que el parser produce de verdad para esas frases.
MAGIC_PANEL = [
    ("duelo", "tema denso",
     dict(semantic_query="grief, mourning, loss, quiet sorrow, contemplative")),
    ("soledad urbana", "tema denso",
     dict(semantic_query="loneliness, isolation, urban alienation, solitude")),
    ("giallo italiano", "tema denso",
     dict(semantic_query="giallo, stylish murder mystery, lurid", countries=["Italy"])),
    ("slasher 80s", "tema denso",
     dict(semantic_query="slasher, masked killer, teenagers stalked, gore",
          year_min=1980, year_max=1989)),
    ("thrillers coreanos", "tema denso",
     dict(semantic_query="thriller, suspense, mystery, crime", countries=["South Korea"])),

    ("atracos 70s", "tema estrecho",
     dict(semantic_query="heist, stylish, caper, robbery, crime",
          year_min=1970, year_max=1979)),
    ("kung fu 70s", "tema estrecho",
     dict(semantic_query="martial arts, kung fu, duels, revenge, shaolin",
          year_min=1970, year_max=1979)),
    ("anime 90s", "tema estrecho",
     dict(semantic_query="anime, hand-drawn animation, japanese, fantastical",
          countries=["Japan"], year_min=1990, year_max=1999)),
    ("noir 40s", "tema estrecho",
     dict(semantic_query="film noir, private eye, femme fatale, corruption",
          year_min=1940, year_max=1949)),

    ("cyberpunk 50s", "interseccion vacia",
     dict(semantic_query="cyberpunk, neon, hackers, megacorporations, dystopia",
          year_min=1950, year_max=1959)),
    ("zombis 40s", "interseccion vacia",
     dict(semantic_query="zombie outbreak, undead, survival horror",
          year_min=1940, year_max=1949)),
    ("atracos europeos 70s", "interseccion vacia",
     dict(semantic_query="heist, stylish, caper, robbery, crime",
          countries=["France", "Germany", "Italy", "Spain", "United Kingdom"],
          year_min=1970, year_max=1979)),
    ("found footage 60s", "interseccion vacia",
     dict(semantic_query="found footage, handheld camera, first person horror",
          year_min=1960, year_max=1969)),

    ("no se que ver", "sin tema",
     dict(semantic_query="no se que ver", open_request=True)),
    ("muy bien valoradas", "sin tema",
     dict(semantic_query="muy bien valoradas", min_vectorbox_score=75)),
    ("para ver en familia", "sin tema",
     dict(semantic_query="family friendly, gentle, wholesome",
          audience_request=True, include_genres=["Family", "Animation"])),

    ("sinsentido", "no responde",
     dict(semantic_query="receta de tortilla de patatas")),
    ("teclado", "no responde",
     dict(semantic_query="asdkjh qwe zxcvbn")),
]

# "Mas como esto": el flujo que el usuario ve como `SEMANTIC Movies like X`.
SIMILAR_PANEL = [
    ("james bond", True), ("Inception", False), ("Suspiria", False),
    ("el padrino", True), ("Mother", False), ("Plan 9 from Outer Space", False),
]


def spread(results):
    """Score range, tolerating the branches that report no distance at all.

    `score is None` is the catalogue branch saying "no query vector reached me",
    which is the honest answer there and not a missing value to paper over.
    """
    scores = [r["score"] for r in results if r.get("score") is not None]
    if not scores:
        return ("s/d", "s/d") if results else ("-", "-")
    return f"{max(scores):.0f}", f"{min(scores):.0f}"


async def audit_magic(tmdb, qdrant, emb):
    print("\n" + "=" * 78)
    print("  MAGIC BOX — normalize(cos) x peso_VBS")
    print("=" * 78)
    print(f"  {'consulta':<22} {'forma':<18} {'dentro':>6} {'+fuera':>6} "
          f"{'max':>4} {'min':>4}  top-3 dentro de los filtros")
    shape = None
    rows = []
    for label, forma, kwargs in MAGIC_PANEL:
        if forma != shape:
            print()
            shape = forma
        intent = MovieSearchIntent(reasoning="forced (audit)", **kwargs)
        async with AsyncSessionLocal() as db:
            resp = await _run_natural_search(
                SearchRequest(query=label, forced_intent=intent),
                None, db, tmdb, qdrant, emb,
            )
        res = resp.results or []
        inside = [m for m in res if not m.get("outside_filters")]
        outside = [m for m in res if m.get("outside_filters")]
        hi, lo = spread(inside)
        top = ", ".join(m["title"] for m in inside[:3])
        flag = "  [RECHAZA]" if resp.low_confidence else ""
        if resp.relaxed_filter:
            flag = f"  [relajado: {resp.relaxed_filter}]"
        print(f"  {label:<22} {forma:<18} {len(inside):>6} {len(outside):>+6} "
              f"{hi:>4} {lo:>4}  {top[:34]}{flag}")
        if outside:
            print(f"  {'':<22} {'':<18} {'':>6} {'':>6} {'':>4} {'':>4}  "
                  f"fuera: {', '.join(m['title'] for m in outside[:3])[:46]}")
        rows.append((label, forma, len(res), res))
    return rows


async def audit_similar(qdrant):
    print("\n" + "=" * 78)
    print("  MAS COMO ESTO — normalize(cos), SIN peso de calidad y SIN acantilado")
    print("=" * 78)
    print(f"  {'semilla':<24} {'->':<26} {'n':>3} {'max':>4} {'min':>4}")
    for needle, substring in SIMILAR_PANEL:
        async with AsyncSessionLocal() as db:
            seed = await _pick_reference_movie(db, needle, substring=substring)
        if not seed:
            print(f"  {needle:<24} {'(sin match)':<26}")
            continue
        resp = await _item_to_item_search(seed.tmdb_id, seed.title, qdrant)
        res = (resp.results if resp else []) or []
        hi, lo = spread(res)
        print(f"  {needle:<24} {seed.title[:25]:<26} {len(res):>3} {hi:>4} {lo:>4}")


async def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--panel", choices=["magic", "similar", "all"], default="all")
    args = ap.parse_args()

    tmdb, qdrant, emb = TMDBClient(), QdrantService(), EmbeddingService()
    if args.panel in ("magic", "all"):
        await audit_magic(tmdb, qdrant, emb)
    if args.panel in ("similar", "all"):
        await audit_similar(qdrant)
    print()


if __name__ == "__main__":
    asyncio.run(main())
