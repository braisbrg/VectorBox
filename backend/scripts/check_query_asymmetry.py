"""Embedding asymmetry empirical test (qdrant-search-quality finding #3).

The catalog encodes each film from its `cinematic_description` (~80 words,
Groq-enriched). Magic Search encodes the user's query on the fly with
`text_override=intent.semantic_query` — short and stylistically different
from the long catalog descriptions. This script measures whether that
asymmetry costs us hits@k.

For each TST-2 anchor we re-encode in three ways and run the same
top-K query against the production Qdrant, then count how many of the
hand-curated expected neighbours appear:

  STORED  — fetch the vector Qdrant already has (the reference).
  DESCR   — re-encode from the film's own cinematic_description.
            Tests symmetric retrieval. Should be ~indistinguishable
            from STORED (and surfaces vector drift if not).
  TITLE   — encode the title alone. Proxy for a user typing "Inception"
            into Magic Box with no LLM expansion. Worst case.

A large gap STORED -> TITLE means the LLM-driven query expansion in
`nlp_search.parse_user_intent` is load-bearing (no expansion = degraded
results); a small gap means the embedding model handles asymmetry well.

Read-only. Usage:
    docker compose exec backend python scripts/check_query_asymmetry.py [--k 10]
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import or_, select
from qdrant_client.models import SearchParams

from config import AsyncSessionLocal
from models.database import Movie
from services.embedding_service import EmbeddingService
from services.qdrant_service import QdrantService


# Same anchors + expected neighbours as backend/tests/test_embeddings_golden_set.py
ANCHORS_AND_NEIGHBOURS = {
    "Howl's Moving Castle": [
        "Spirited Away", "Castle in the Sky", "Princess Mononoke",
        "Mary and the Witch's Flower", "Ponyo", "Kiki's Delivery Service",
        "The Cat Returns", "From Up on Poppy Hill", "The Wind Rises",
        "My Neighbor Totoro", "Earwig and the Witch",
    ],
    "Deprisa, deprisa": [
        "Navajeros", "Perros callejeros", "El pico", "Yo, 'El Vaquilla'",
        "El Lute: camina o revienta", "Maravillas", "Colegas",
        "El pico 2", "Barrio", "Los olvidados",
    ],
    "Pan's Labyrinth": [
        "The Devil's Backbone", "The Shape of Water", "Crimson Peak",
        "The Orphanage", "Cronos", "Hellboy", "Mama", "Pinocchio",
    ],
    "Inception": [
        "Tenet", "Interstellar", "The Matrix", "Memento", "Shutter Island",
        "Eternal Sunshine of the Spotless Mind", "The Prestige", "Source Code",
        "Predestination",
    ],
    "The Godfather": [
        "Goodfellas", "Casino", "The Departed", "Once Upon a Time in America",
        "Heat", "Scarface", "A Bronx Tale", "Donnie Brasco",
    ],
    "Goodfellas": [
        "The Godfather", "Casino", "The Departed",
        "Once Upon a Time in America", "Heat", "American Gangster",
        "Donnie Brasco",
    ],
    "Spirited Away": [
        "Howl's Moving Castle", "Castle in the Sky", "Princess Mononoke",
        "My Neighbor Totoro", "Kiki's Delivery Service", "Ponyo",
        "Mary and the Witch's Flower", "The Cat Returns",
    ],
}


async def _resolve(title: str) -> Movie | None:
    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            select(Movie).where(
                or_(Movie.title.ilike(title), Movie.original_title.ilike(title))
            ).limit(1)
        )).scalar_one_or_none()
    return row


def _count_hits(titles: list[str], expected_lower: set[str]) -> int:
    return sum(1 for t in titles if (t or "").lower() in expected_lower)


async def _topk_titles(qd: QdrantService, vector: list[float], k: int) -> list[str]:
    """Top-(k+1) titles by Qdrant query; caller skips rank 0 (the anchor itself)."""
    res = await qd.client.query_points(
        collection_name=qd.COLLECTION_NAME,
        query=vector,
        limit=k + 1,
        search_params=SearchParams(hnsw_ef=128),
    )
    return [(p.payload.get("title") or "") for p in res.points]


async def main(k: int) -> int:
    qd = QdrantService()
    emb = EmbeddingService()

    print(f"Embedding asymmetry probe: k={k}\n")
    print(f"{'anchor':32s}  {'STORED':>7s} {'DESCR':>7s} {'TITLE':>7s}  expected")

    totals = {"STORED": 0, "DESCR": 0, "TITLE": 0}
    universe = 0

    for anchor, neighbours in ANCHORS_AND_NEIGHBOURS.items():
        movie = await _resolve(anchor)
        if movie is None:
            print(f"  [skip] {anchor!r}: not in DB")
            continue

        expected_lower = {n.lower() for n in neighbours}
        universe += len(neighbours)

        # STORED — what Qdrant has indexed for this point
        stored_vec = await qd.get_vector(movie.tmdb_id)
        if stored_vec is None:
            print(f"  [skip] {anchor!r}: no stored vector")
            continue
        stored_titles = await _topk_titles(qd, stored_vec, k)
        n_stored = _count_hits(stored_titles[1:k + 1], expected_lower)

        # DESCR — re-encode from cinematic_description (or fallback recipe)
        descr_text = movie.cinematic_description or " ".join(filter(None, [
            movie.overview or "",
            " ".join(movie.genres or []),
            " ".join(movie.keywords or []),
        ]))
        descr_vec = emb.generate_embedding(
            {"overview": movie.overview or "", "genres": movie.genres or [], "keywords": movie.keywords or []},
            text_override=descr_text,
        ).tolist()
        descr_titles = await _topk_titles(qd, descr_vec, k)
        # Filter the anchor itself if it shows up (it usually does at rank 0)
        descr_titles_excl = [t for t in descr_titles if t.lower() != anchor.lower()][:k]
        n_descr = _count_hits(descr_titles_excl, expected_lower)

        # TITLE — encode just the title (proxy for a non-expanded short query)
        title_vec = emb.generate_embedding(
            {"overview": "", "genres": [], "keywords": []},
            text_override=anchor,
        ).tolist()
        title_titles = await _topk_titles(qd, title_vec, k)
        title_titles_excl = [t for t in title_titles if t.lower() != anchor.lower()][:k]
        n_title = _count_hits(title_titles_excl, expected_lower)

        totals["STORED"] += n_stored
        totals["DESCR"] += n_descr
        totals["TITLE"] += n_title

        print(f"  {anchor!r:32s}  {n_stored:>7d} {n_descr:>7d} {n_title:>7d}  /{len(neighbours)}")

    print(f"\n  {'TOTAL':32s}  {totals['STORED']:>7d} {totals['DESCR']:>7d} {totals['TITLE']:>7d}  /{universe}")
    if universe:
        for k_name in ("STORED", "DESCR", "TITLE"):
            pct = totals[k_name] / universe * 100
            print(f"  {k_name}: {totals[k_name]}/{universe} = {pct:.1f}%")

        # Heuristic interpretation
        stored_p = totals["STORED"] / universe
        title_p = totals["TITLE"] / universe
        if stored_p == 0:
            print("\n[no signal — STORED returned no hits, can't compare]")
        else:
            drop = (stored_p - title_p) / stored_p * 100
            print(f"\nTITLE-vs-STORED drop: {drop:.0f}%")
            if drop >= 30:
                print("=> LLM query-expansion in nlp_search.parse_user_intent is LOAD-BEARING.")
                print("   Short queries without expansion will likely degrade hard.")
            elif drop >= 10:
                print("=> Mild asymmetry. LLM expansion helps but isn't strictly required.")
            else:
                print("=> Asymmetry within noise. Embedding model handles short queries well.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--k", type=int, default=10)
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.k)))
