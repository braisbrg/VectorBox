"""Embedding experiment: compare 5 variants on a curated pool of films.

Variants:
  Baseline — current Qdrant state (all-MiniLM-L6-v2, WITH title)
  A — all-MiniLM-L6-v2, NO title (cheapest fix)
  B — paraphrase-multilingual-MiniLM-L12-v2, NO title (multilingual drop-in)
  C — intfloat/multilingual-e5-small, NO title (with `query:` prefix)
  D — google/embeddinggemma-300m, NO title (SOTA <500M, 768-dim)

Pool: ~7 anchors + their thematic neighbours + ~30 distractors.
For each anchor we print top-8 neighbours in each variant; films marked ✓ if
they belong to the curated "expected good neighbours" set.

Usage:
    docker compose exec backend python scripts/experiment_embeddings.py
"""
import asyncio
import os
import sys

sys.path.append(os.getcwd())

import numpy as np
from sqlalchemy import select, or_, func
from sentence_transformers import SentenceTransformer

from config import AsyncSessionLocal
from models.database import Movie
from services.qdrant_service import QdrantService

# ---------------- curated pool ----------------

ANCHORS = [
    "Howl's Moving Castle",
    "Deprisa, deprisa",
    "Pan's Labyrinth",
    "Inception",
    "The Godfather",
    "Goodfellas",
    "Spirited Away",
]

EXPECTED_NEIGHBOURS = {
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

DISTRACTORS = [
    "Blade Runner", "Star Wars", "Aliens", "The Fifth Element",
    "The Conjuring", "Hereditary", "It Follows",
    "Superbad", "Anchorman", "Borat", "The Hangover",
    "The Notebook", "Titanic", "Pretty Woman",
    "Die Hard", "Mad Max: Fury Road", "John Wick", "The Raid",
    "Toy Story", "Up", "WALL·E", "Coco", "Moana",
    "Forrest Gump", "Schindler's List", "12 Angry Men",
    "The Shawshank Redemption", "Parasite", "Whiplash",
    "La La Land", "Birdman", "Avatar", "Jurassic Park",
    "Spider-Man", "Iron Man", "Frozen", "The Lion King",
]

# ---------------- helpers ----------------


def make_text(m: Movie, include_title: bool) -> str:
    parts = []
    if include_title and m.title:
        parts.append(m.title)
    if m.overview:
        parts.append(m.overview)
    if m.genres:
        parts.append(f"Genres: {', '.join(m.genres)}")
    if m.keywords:
        parts.append(f"Themes: {', '.join((m.keywords or [])[:15])}")
    return ". ".join(parts)


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


# ---------------- main ----------------


async def fetch_pool():
    """Look up every film in the curated pool against title or original_title."""
    all_titles = set(ANCHORS)
    for v in EXPECTED_NEIGHBOURS.values():
        all_titles.update(v)
    all_titles.update(DISTRACTORS)

    found, missing = [], []
    seen_ids = set()
    async with AsyncSessionLocal() as db:
        for t in all_titles:
            r = await db.execute(
                select(Movie).where(
                    or_(
                        func.lower(Movie.title) == t.lower(),
                        func.lower(Movie.original_title) == t.lower(),
                    )
                ).limit(1)
            )
            m = r.scalars().first()
            if m and m.id not in seen_ids:
                found.append((t, m))  # remember which curated label fetched this row
                seen_ids.add(m.id)
            elif not m:
                missing.append(t)
    return found, missing


def title_lookup(curated_to_movie: dict, label: str) -> str:
    """Resolve a curated label to the row's title (which is what we key vectors by)."""
    m = curated_to_movie.get(label)
    return m.title if m else None


async def baseline_vectors(movies):
    """Pull existing Qdrant vectors for the pool — that's the production state."""
    qd = QdrantService()
    out = {}
    for m in movies:
        v = await qd.get_vector(m.tmdb_id)
        if v:
            out[m.title] = np.array(v, dtype=np.float32)
    return out


def encode_with_model(model_name: str, movies, prefix: str = "") -> dict:
    print(f"  Loading model: {model_name}")
    model = SentenceTransformer(model_name)
    texts, keys = [], []
    for m in movies:
        t = make_text(m, include_title=False)
        if not t.strip():
            continue
        texts.append(prefix + t if prefix else t)
        keys.append(m.title)
    print(f"  Encoding {len(texts)} films…")
    embs = model.encode(
        texts, convert_to_numpy=True, normalize_embeddings=True, show_progress_bar=False
    )
    return dict(zip(keys, embs))


def topk(vectors: dict, anchor_title: str, k: int = 8) -> list:
    if anchor_title not in vectors:
        return []
    a = vectors[anchor_title]
    sims = [(t, cosine(a, v)) for t, v in vectors.items() if t != anchor_title]
    sims.sort(key=lambda x: -x[1])
    return sims[:k]


async def main():
    found, missing = await fetch_pool()
    print(f"\nPool resolved: {len(found)} films found in DB, {len(missing)} missing.")
    if missing:
        print("Missing (skipped):")
        for t in missing:
            print(f"  - {t}")

    movies = [m for _, m in found]
    curated_to_movie = {label: m for label, m in found}
    print(f"\nProceeding with {len(movies)} films.\n")

    variants = []  # list of (display_name, callable that returns dict[title→vector])

    print("=== Loading variants ===\n")

    print("[Baseline] pulling current Qdrant vectors")
    base_vecs = await baseline_vectors(movies)
    variants.append(("Baseline (Qdrant: MiniLM-L6-v2 WITH title)", base_vecs))

    def try_variant(label: str, model_name: str, prefix: str = ""):
        print(f"\n[{label}] {model_name} NO TITLE" + (f" (prefix={prefix!r})" if prefix else ""))
        try:
            vecs = encode_with_model(model_name, movies, prefix=prefix)
            variants.append((f"{label}: {model_name.split('/')[-1]} NO title", vecs))
        except Exception as e:
            print(f"  !! variant {label} failed: {type(e).__name__}: {str(e)[:200]}")
            print(f"  !! continuing with other variants")

    try_variant("A", "sentence-transformers/all-MiniLM-L6-v2")
    try_variant("B", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
    try_variant("C", "intfloat/multilingual-e5-small", prefix="query: ")
    try_variant("D", "google/embeddinggemma-300m")

    # ---------------- comparison ----------------

    for anchor_label in ANCHORS:
        anchor_title = title_lookup(curated_to_movie, anchor_label)
        if not anchor_title:
            print(f"\n!!! Anchor '{anchor_label}' not in pool, skipping comparison.")
            continue

        expected_titles = {
            (title_lookup(curated_to_movie, l) or l).lower()
            for l in EXPECTED_NEIGHBOURS.get(anchor_label, [])
        }

        print("\n" + "=" * 100)
        print(f"ANCHOR: {anchor_label}  (DB title: {anchor_title})")
        print(f"  Expected: {EXPECTED_NEIGHBOURS.get(anchor_label, [])[:6]}")
        print("=" * 100)

        for variant_name, vecs in variants:
            top = topk(vecs, anchor_title, k=8)
            hits = sum(1 for t, _ in top if t.lower() in expected_titles)
            print(f"\n  {variant_name}  [hits in top-8: {hits}/{len(expected_titles)}]")
            for i, (t, sim) in enumerate(top):
                marker = " ✓" if t.lower() in expected_titles else ""
                print(f"    {i+1:2d}. sim={sim:.3f}  {t}{marker}")


if __name__ == "__main__":
    asyncio.run(main())
