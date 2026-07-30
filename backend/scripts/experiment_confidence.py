"""Can the engine tell when it cannot answer? — 2026-07-29

The problem, measured: "a film parents and kids will both enjoy" returned Boss
Baby, a direct-to-video Charlotte's Web sequel and YES DAY at VBS 44, while "the
loneliness of living in a huge city" returned Wings of Desire and L'Eclisse. The
pipeline treats both the same — it always fills the shelf with 14 films, whether
its best match scored 88 or 64.

The catalogue is embedded on what films are ABOUT. A request with no thematic
content (audience fit, nonsense, a bare mood) has no good vector, and no prompt
wording fixes that: tried on 2026-07-29, Spanish improved and English got worse.

So the question this script answers is narrower and testable:
    IS THE SIMILARITY SIGNAL A RELIABLE DISCRIMINATOR?
If answerable and unanswerable queries separate cleanly on some statistic, a
threshold is defensible. If they overlap, any threshold is a coin flip and the
honest move is to leave the ranking alone.

It runs the real pipeline offline (parse -> embed -> Qdrant -> post-filter ->
blend), so no HTTP, no rate limit, and one LLM parse per query.

    docker compose exec backend python scripts/experiment_confidence.py
    docker compose exec backend python scripts/experiment_confidence.py --repeat 2
"""
import argparse
import asyncio
import logging
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qdrant_client.models import SearchParams
from sqlalchemy import select

from config import AsyncSessionLocal
from models.database import Movie
from services.embedding_service import EmbeddingService
from services.magic_search_ranking import compute_blended_score, movie_passes_post_filter
from services.nlp_search import parse_user_intent
from services.qdrant_service import QdrantService

logging.getLogger().setLevel(logging.ERROR)

# The panel. `expect` is the human judgement the experiment is scored against:
#   answerable  — the catalogue holds films about this; a good answer exists
#   unanswerable— no good answer exists, for any of three different reasons
PANEL = [
    # ── thematic, specific: the engine's home ground ────────────────────────
    ("answerable", "algo lento y triste sobre el duelo, sin sustos"),
    ("answerable", "la soledad de vivir en una ciudad enorme"),
    ("answerable", "atracos con mucho estilo, cine europeo de los 70"),
    ("answerable", "terror psicologico donde nunca ves al monstruo"),
    ("answerable", "peliculas sobre inmigrantes que no encajan en ningun sitio"),
    ("answerable", "ciencia ficcion melancolica sobre la memoria"),
    ("answerable", "westerns crepusculares sobre hombres que ya no sirven"),
    ("answerable", "documentales sobre obsesiones absurdas"),
    # ── audience / suitability: parses fine, has no vector ──────────────────
    ("unanswerable", "una peli que guste a padres e hijos, sin violencia ni sustos"),
    ("unanswerable", "algo que terminemos mis padres y yo sin discutir"),
    ("unanswerable", "una peli para ver de fondo mientras plancho"),
    # ── too vague to mean anything ─────────────────────────────────────────
    ("unanswerable", "algo bueno"),
    ("unanswerable", "una peli"),
    ("unanswerable", "sorprendeme"),
    # ── nonsense and off-domain ────────────────────────────────────────────
    ("unanswerable", "asdkjh qwe zxcvbn"),
    ("unanswerable", "cual es la capital de Francia"),
    ("unanswerable", "342342 8888 ????"),
    ("unanswerable", "receta de tortilla de patatas"),
]


async def run_one(query: str, emb: EmbeddingService, qd: QdrantService) -> dict:
    intent = await parse_user_intent(query)
    vec = emb.generate_embedding(
        {"overview": intent.semantic_query, "genres": intent.include_genres or [], "keywords": []},
        text_override=intent.semantic_query,
    )
    hits = await qd.client.query_points(
        collection_name="movies", query=vec.tolist(), limit=20,
        search_params=SearchParams(hnsw_ef=128),
    )
    tmdb_ids = [h.payload.get("tmdb_id") for h in hits.points if h.payload.get("tmdb_id")]
    db_movies = {}
    if tmdb_ids:
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(select(Movie).where(Movie.tmdb_id.in_(tmdb_ids)))).scalars().all()
            db_movies = {m.tmdb_id: m for m in rows}

    kept = []
    for h in hits.points:
        m = db_movies.get(h.payload.get("tmdb_id") or h.id)
        if m is None or not movie_passes_post_filter(m, intent):
            continue
        final, _ts, _w = compute_blended_score(
            raw_cosine=h.score, query=query, intent=intent,
            title=m.title or "", vbs=m.vectorbox_score,
        )
        kept.append((final, h.score, m))

    kept.sort(key=lambda x: x[0], reverse=True)
    raw = [h.score for h in hits.points]          # every neighbour Qdrant offered
    top = [k[1] for k in kept[:10]]               # cosines that survived filtering
    vbs = [(k[2].vectorbox_score or 0) for k in kept[:10]]
    return {
        "query": query,
        "n": len(kept),
        "raw_max": max(raw) if raw else 0.0,
        "raw_mean10": statistics.fmean(raw[:10]) if raw else 0.0,
        "kept_mean": statistics.fmean(top) if top else 0.0,
        "vbs_mean": statistics.fmean(vbs) if vbs else 0.0,
        "titles": [k[2].title for k in kept[:3]],
        "semantic": (intent.semantic_query or "")[:64],
    }


def separation(rows: list[dict], key: str) -> tuple[float, float, float, str]:
    """How cleanly does one statistic split the two groups?

    Returns (best answerable-min, worst unanswerable-max, gap, verdict). A
    positive gap means a threshold exists that never misclassifies either side.
    """
    a = [r[key] for r in rows if r["expect"] == "answerable"]
    u = [r[key] for r in rows if r["expect"] == "unanswerable"]
    lo_a, hi_u = min(a), max(u)
    gap = lo_a - hi_u
    verdict = "SEPARA LIMPIO" if gap > 0 else f"se solapan ({-gap:.3f})"
    return lo_a, hi_u, gap, verdict


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repeat", type=int, default=1,
                    help="runs per query; the parser is non-deterministic, so >1 shows the spread")
    args = ap.parse_args()

    emb = EmbeddingService()
    qd = QdrantService()

    rows = []
    print(f"Panel de {len(PANEL)} consultas × {args.repeat} pasada(s)\n")
    for expect, q in PANEL:
        for _ in range(args.repeat):
            r = await run_one(q, emb, qd)
            r["expect"] = expect
            rows.append(r)
            flag = "·" if expect == "answerable" else "✗"
            print(f"  {flag} {q[:52]:52s} n={r['n']:2d} rawmax={r['raw_max']:.3f} "
                  f"raw10={r['raw_mean10']:.3f} vbs={r['vbs_mean']:.0f}  {', '.join(r['titles'][:2])[:46]}")

    print("\n" + "=" * 78)
    print("¿QUÉ ESTADÍSTICO SEPARA LO RESPONDIBLE DE LO QUE NO?\n")
    print(f"  {'estadístico':<14} {'mín respondible':>16} {'máx no-resp.':>14} {'margen':>9}   veredicto")
    for key, label in [("raw_max", "raw_max"), ("raw_mean10", "raw_mean@10"),
                       ("kept_mean", "kept_mean"), ("vbs_mean", "vbs_mean")]:
        lo, hi, gap, verdict = separation(rows, key)
        print(f"  {label:<14} {lo:>16.3f} {hi:>14.3f} {gap:>+9.3f}   {verdict}")

    print("\n  Un margen positivo significa que existe un umbral que no se equivoca")
    print("  con ninguna de las dos categorías. Uno negativo significa que cualquier")
    print("  umbral clasificará mal alguna consulta, y entonces la puerta miente.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
