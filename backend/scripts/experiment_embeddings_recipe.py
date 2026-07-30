"""Targeted experiment: test the missing combo embeddinggemma-300m × cinematic_description.

The main experiment found embeddinggemma + (overview+genres+keywords) >> baseline.
But baseline uses cinematic_description + MiniLM. This isolates whether the win
is from the model or from dropping cinematic_description.

Variants:
  X1 — embeddinggemma + cinematic_description (the missing combo)
  X2 — embeddinggemma + overview+genres+keywords (= variant D from main script)
  X3 — MiniLM + cinematic_description (= current production baseline)
  X4 — MiniLM + overview+genres+keywords (= variant A from main script)

Same anchor pool as experiment_embeddings.py.
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


def cosine(a, b):
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def text_cinematic(m: Movie) -> str | None:
    return (m.cinematic_description or "").strip() or None


def text_simple(m: Movie) -> str | None:
    parts = []
    if m.overview:
        parts.append(m.overview)
    if m.genres:
        parts.append(f"Genres: {', '.join(m.genres)}")
    if m.keywords:
        parts.append(f"Themes: {', '.join((m.keywords or [])[:15])}")
    return ". ".join(parts).strip() or None


async def fetch_pool():
    all_titles = set(ANCHORS)
    for v in EXPECTED_NEIGHBOURS.values():
        all_titles.update(v)
    all_titles.update(DISTRACTORS)
    found = []
    seen = set()
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
            if m and m.id not in seen:
                found.append((t, m))
                seen.add(m.id)
    return found


def encode(model, movies, text_fn):
    texts, keys = [], []
    for m in movies:
        t = text_fn(m)
        if t:
            texts.append(t)
            keys.append(m.title)
    embs = model.encode(texts, convert_to_numpy=True, normalize_embeddings=True, show_progress_bar=False)
    return dict(zip(keys, embs))


def topk(vecs, anchor_title, k=8):
    if anchor_title not in vecs:
        return []
    a = vecs[anchor_title]
    sims = [(t, cosine(a, v)) for t, v in vecs.items() if t != anchor_title]
    sims.sort(key=lambda x: -x[1])
    return sims[:k]


async def main():
    found = await fetch_pool()
    curated_to_movie = {label: m for label, m in found}
    movies = [m for _, m in found]
    print(f"Pool: {len(movies)} films\n")

    print("Loading MiniLM…")
    minilm = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
    print("Loading embeddinggemma-300m…")
    gemma = SentenceTransformer("google/embeddinggemma-300m")

    variants = []
    print("\nEncoding X1: gemma + cinematic_description")
    variants.append(("X1: gemma + cinematic", encode(gemma, movies, text_cinematic)))
    print("Encoding X2: gemma + simple (overview+genres+kws)")
    variants.append(("X2: gemma + simple   ", encode(gemma, movies, text_simple)))
    print("Encoding X3: MiniLM + cinematic (= production baseline)")
    variants.append(("X3: MiniLM + cinematic", encode(minilm, movies, text_cinematic)))
    print("Encoding X4: MiniLM + simple")
    variants.append(("X4: MiniLM + simple   ", encode(minilm, movies, text_simple)))

    totals = {name: 0 for name, _ in variants}
    for anchor_label in ANCHORS:
        m = curated_to_movie.get(anchor_label)
        if not m:
            continue
        anchor_title = m.title
        expected = {
            (curated_to_movie.get(l).title if curated_to_movie.get(l) else l).lower()
            for l in EXPECTED_NEIGHBOURS.get(anchor_label, [])
        }
        print(f"\n=== {anchor_label} ===")
        for name, vecs in variants:
            top = topk(vecs, anchor_title, 8)
            hits = sum(1 for t, _ in top if t.lower() in expected)
            totals[name] += hits
            top_str = ", ".join(f"{t}{'✓' if t.lower() in expected else ''}" for t, _ in top[:5])
            print(f"  {name}: {hits}/{len(expected)}  → {top_str}")

    print("\n" + "=" * 70)
    print("TOTALS (hits across all anchors, top-8 each):")
    for name, total in sorted(totals.items(), key=lambda x: -x[1]):
        print(f"  {name}: {total}")


if __name__ == "__main__":
    asyncio.run(main())
