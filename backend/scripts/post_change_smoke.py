"""Post-change smoke test — one-shot sanity check for the 2026-05-15 work.

Runs through every system the day's changes touched and prints a green /
yellow / red line per check. NOT a replacement for the pytest suite (which
covers behaviour); this is the "did production survive my changes?" eyeball
pass that can be run after a deploy or after `docker compose up -d --build`.

Usage:
    docker compose exec backend python scripts/post_change_smoke.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, text, func
from qdrant_client.models import SearchParams

from config import AsyncSessionLocal
from models.database import Movie, User, UserRating, ZipUpload
from services.embedding_service import EmbeddingService, get_model
from services.qdrant_service import QdrantService


GREEN = "\033[92m✓"
YELLOW = "\033[93m⚠"
RED = "\033[91m✗"
RESET = "\033[0m"


def line(status, label, detail=""):
    print(f"  {status} {label:50s}{RESET}  {detail}")


async def check_embedding_model_alignment():
    print("\n=== embedding model alignment ===")
    expected = "google/embeddinggemma-300m"
    expected_dim = 768
    model_name = EmbeddingService.MODEL_NAME
    if model_name == expected:
        line(GREEN, "EmbeddingService.MODEL_NAME", expected)
    else:
        line(RED, "EmbeddingService.MODEL_NAME", f"got {model_name!r}, expected {expected!r}")
        return

    v = get_model().encode("smoke test", convert_to_numpy=True)
    if v.shape == (expected_dim,):
        line(GREEN, "runtime encoding dim", str(v.shape))
    else:
        line(RED, "runtime encoding dim", f"got {v.shape}, expected ({expected_dim},)")

    qd = QdrantService()
    info = await qd.client.get_collection("movies")
    if info.config.params.vectors.size == expected_dim:
        line(GREEN, "Qdrant collection vector size", str(info.config.params.vectors.size))
    else:
        line(RED, "Qdrant collection vector size", f"got {info.config.params.vectors.size}, expected {expected_dim}")

    cnt = (await qd.client.count(collection_name="movies", exact=True)).count
    if cnt >= 7000:
        line(GREEN, "Qdrant point count", str(cnt))
    else:
        line(YELLOW, "Qdrant point count", f"low: {cnt}")


async def check_catalog_metadata_coverage():
    print("\n=== catalog metadata coverage (post-refresh) ===")
    async with AsyncSessionLocal() as db:
        total = await db.scalar(select(func.count(Movie.id)))
        checks = [
            ("imdb_rating", "imdb_rating IS NOT NULL", 90),
            ("tagline", "tagline IS NOT NULL", 80),
            ("mpaa_rating", "mpaa_rating IS NOT NULL", 80),
            ("awards_text", "awards_text IS NOT NULL", 80),
            ("omdb_countries", "omdb_countries IS NOT NULL", 95),
            ("backdrop_path", "backdrop_path IS NOT NULL", 95),
            ("oscar_wins > 0", "oscar_wins > 0", 5),
            ("collection_id", "collection_id IS NOT NULL", 20),
            ("is_adult = true", "is_adult = true", 0, "max"),
        ]
        for chk in checks:
            col_label, sql_where, threshold = chk[:3]
            mode = chk[3] if len(chk) > 3 else "min"
            n = await db.scalar(text(f"SELECT COUNT(*) FROM movies WHERE {sql_where}"))
            pct = 100 * n / total if total else 0
            detail = f"{n}/{total} ({pct:.1f}%)"
            ok = (pct >= threshold) if mode == "min" else (pct <= threshold)
            line(GREEN if ok else YELLOW, f"coverage {col_label}", detail)


async def check_embedding_quality_distribution():
    print("\n=== embedding_quality_score distribution ===")
    async with AsyncSessionLocal() as db:
        r = await db.execute(text(
            "SELECT AVG(embedding_quality_score) AS mean, "
            "COUNT(*) FILTER (WHERE embedding_quality_score < 0.35) AS bad, "
            "COUNT(*) AS total FROM movies"
        ))
        row = r.first()
        mean, bad, total = row[0] or 0, row[1] or 0, row[2] or 0
        if 0.5 <= mean <= 0.9:
            line(GREEN, "mean quality_score", f"{mean:.3f}")
        else:
            line(YELLOW, "mean quality_score", f"{mean:.3f} (expected 0.5-0.9)")
        if bad < 100:
            line(GREEN, "films with quality_score < 0.35", str(bad))
        else:
            line(YELLOW, "films with quality_score < 0.35", str(bad))


async def check_user_state_integrity():
    print("\n=== user / rating integrity ===")
    async with AsyncSessionLocal() as db:
        # B-18: no duplicate (user, movie) pairs
        dupes = await db.scalar(text(
            "SELECT COUNT(*) FROM (SELECT user_id, movie_id FROM user_ratings "
            "GROUP BY 1,2 HAVING COUNT(*) > 1) sub"
        ))
        if dupes == 0:
            line(GREEN, "user_ratings UNIQUE(user_id, movie_id) holds", "0 duplicates")
        else:
            line(RED, "user_ratings duplicate pairs", str(dupes))

        # B-20: every user with ≥15 ratings has onboarding_completed=true
        leaking = await db.scalar(text(
            "SELECT COUNT(*) FROM users u WHERE NOT u.onboarding_completed "
            "AND (SELECT COUNT(*) FROM user_ratings WHERE user_id = u.id) >= 15"
        ))
        if leaking == 0:
            line(GREEN, "onboarding_completed in sync (≥15 ratings)", "0 leaking")
        else:
            line(YELLOW, "users with ≥15 ratings but not flagged", str(leaking))


async def check_qdrant_recall_floor():
    print("\n=== Qdrant golden-set recall (TST-2 mirror) ===")
    # Light version of test_embeddings_golden_set — 3 anchors only.
    anchors = {
        "Howl's Moving Castle": ["Spirited Away", "My Neighbor Totoro", "The Wind Rises", "Castle in the Sky", "Princess Mononoke"],
        "The Godfather": ["Goodfellas", "Casino", "The Departed", "Scarface", "A Bronx Tale"],
        "Inception": ["Tenet", "Interstellar", "The Matrix", "Memento", "Shutter Island"],
    }
    qd = QdrantService()
    total_hits = 0
    async with AsyncSessionLocal() as db:
        for anchor, expected in anchors.items():
            row = (await db.execute(
                select(Movie.tmdb_id).where(Movie.title.ilike(anchor)).limit(1)
            )).first()
            if not row:
                line(YELLOW, f"anchor {anchor!r}", "not in DB")
                continue
            v = await qd.get_vector(row[0])
            if v is None:
                line(YELLOW, f"anchor {anchor!r}", "no vector")
                continue
            hits = await qd.client.query_points(
                collection_name="movies", query=v, limit=21,
                search_params=SearchParams(hnsw_ef=128),
            )
            got = {(h.payload.get("title") or "").lower() for h in hits.points[1:21]}
            expected_lower = {e.lower() for e in expected}
            n = len(got & expected_lower)
            total_hits += n
            line(GREEN if n >= 1 else YELLOW, f"anchor {anchor!r}", f"hits@20 = {n}/{len(expected)}")
    if total_hits >= 5:
        line(GREEN, "TOTAL hits across 3 anchors", str(total_hits))
    else:
        line(YELLOW, "TOTAL hits across 3 anchors", str(total_hits))


async def check_zip_idempotency_table():
    print("\n=== zip_uploads (F-35) ===")
    async with AsyncSessionLocal() as db:
        # Table exists and is queryable
        try:
            n = await db.scalar(select(func.count(ZipUpload.id)))
            line(GREEN, "zip_uploads table queryable", f"{n} rows")
        except Exception as e:
            line(RED, "zip_uploads", str(e))


async def main():
    print("=" * 60)
    print("POST-CHANGE SMOKE TEST")
    print("=" * 60)
    await check_embedding_model_alignment()
    await check_catalog_metadata_coverage()
    await check_embedding_quality_distribution()
    await check_user_state_integrity()
    await check_qdrant_recall_floor()
    await check_zip_idempotency_table()
    print("\n" + "=" * 60)


asyncio.run(main())
