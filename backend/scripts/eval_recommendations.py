"""Paired benchmark for recommendation changes — the instrument, not a result.

Six decisions on 2026-08-03/04 came out of this harness (group fusion, the
neighbour table, Hidden Gems, the radar, More Like This, the display scale). It
lived in a scratchpad and died with the session; this is that harness, kept.

WHY IT LOOKS LIKE THIS — every guard here exists because its absence produced a
wrong answer during that session:

  paired      The same method, same group, different random split swings **24
              points** (24.3% -> 48.1%). Comparing means across two runs is
              reading noise, and that is exactly what produced a chain of
              self-refutations. Every scorer is evaluated on the SAME group,
              split, mask and test set, and what gets reported is the mean of
              the DIFFERENCES with a 95% CI.
  --- azar    A random ranking MUST land at ~50%. If it does not, the metric is
              broken and nothing else on the page means anything. It was the
              last thing checked that session; it should be the first.
  por usuario A pooled mean hid that the <50k-vote universe leaves 8 of 12
              profiles with 0-5 evaluable films, so "12 profiles" was really 4
              and one user supplied ~112 of ~150 test films.
  ruido       Prints how much the same scorer moves across splits. Anything
              smaller than that is not a finding.

WHAT IT CANNOT SEE: quality, coherence, and whether a list is embarrassing. The
metric rewards ranking films the user already watched, so it loves the canon —
it once preferred a variant that put The Godfather in every list. Always eyeball
the actual output before believing a win.

    docker compose exec backend python scripts/eval_recommendations.py
    docker compose exec backend python scripts/eval_recommendations.py --sizes 2,3,5 --universe full
"""
import argparse
import asyncio
import itertools
import logging
import os
import random
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select  # noqa: E402

from config import AsyncSessionLocal  # noqa: E402
from models.database import Movie, User, UserRating  # noqa: E402
from services.qdrant_service import QdrantService  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("eval")

MIN_LIBRARY = 15   # films a profile needs to take part at all
MIN_TEST = 5       # evaluable held-out films a member needs to be scored


def _dense(point):
    """The dense vector, whatever shape Qdrant returns (named vectors since the
    2026-08-03 sparse migration make `point.vector` a dict for most points)."""
    v = point.vector
    if isinstance(v, dict):
        for x in v.values():
            if hasattr(x, "__len__") and len(x) == QdrantService.VECTOR_SIZE:
                return x
        return None
    return v if v is not None and len(v) == QdrantService.VECTOR_SIZE else None


def _norm(X):
    return X / np.linalg.norm(X, axis=-1, keepdims=True)


def _z(x):
    return (x - x.mean()) / (x.std() + 1e-9)


def ci95(values):
    a = np.asarray(values, dtype=float)
    if len(a) < 3:
        return float("nan"), float("nan"), float("nan")
    se = a.std(ddof=1) / np.sqrt(len(a))
    return a.mean(), a.mean() - 1.96 * se, a.mean() + 1.96 * se


async def _load(db, qdrant):
    points, offset = [], None
    while True:   # paginated: a 20k scroll with vectors times out when Qdrant is busy
        batch, offset = await qdrant.client.scroll(
            collection_name=qdrant.COLLECTION_NAME, limit=2000, offset=offset,
            with_payload=False, with_vectors=True,
        )
        points.extend(batch)
        if offset is None:
            break
    points = [p for p in points if _dense(p) is not None]
    ids = np.array([int(p.id) for p in points])
    pos = {int(t): i for i, t in enumerate(ids)}
    V = _norm(np.array([_dense(p) for p in points], dtype=np.float32))

    vbs = np.zeros(len(V))
    pop = np.zeros(len(V))
    for tid, score, votes in (await db.execute(
        select(Movie.tmdb_id, Movie.vectorbox_score, Movie.imdb_vote_count)
    )).all():
        if tid in pos:
            vbs[pos[tid]] = score or 0.0
            pop[pos[tid]] = votes or 0.0

    profiles = {}
    for uid, uname in (await db.execute(select(User.id, User.username))).all():
        loved = [i for i in (await db.execute(
            select(Movie.tmdb_id).join(UserRating, Movie.id == UserRating.movie_id).where(
                UserRating.user_id == uid,
                (UserRating.rating >= 4.0) | (UserRating.is_liked.is_(True)),
            )
        )).scalars().all() if i in pos]
        if len(loved) >= MIN_LIBRARY:
            profiles[uname] = [pos[i] for i in sorted(set(loved))]
    return ids, pos, V, vbs, pop, profiles


def build_scorers(V, vbs, pop, rng):
    """Name -> scorer(seed_index_lists) -> score per catalogue film.

    Add whatever you are testing here. The `--- azar` anchor is not optional:
    it is what proves the metric still works.
    """
    G = V.mean(axis=0)
    G /= np.linalg.norm(G)
    Vc = _norm(V - 0.5 * np.outer(V @ G, G))
    zq = _z(np.clip(vbs, 0, 100))

    def centroid(seeds):
        return np.mean([V @ (V[s].mean(axis=0) / np.linalg.norm(V[s].mean(axis=0)))
                        for s in seeds], axis=0)

    def fusion(seeds, topk=20, rrf_k=60):
        """Per-film neighbours fused by reciprocal rank — the group-sync shape."""
        acc = np.zeros(len(V))
        for s in seeds:
            M = Vc @ Vc[s].T
            k = min(topk, len(s))
            thr = np.partition(M, -k, axis=0)[-k, :]
            votes = (M * (M >= thr)).sum(axis=1)
            order = np.argsort(-votes)
            rank = np.empty(len(V), int)
            rank[order] = np.arange(len(V))
            acc += 1.0 / (rrf_k + rank + 1)
        return _z(acc)

    return {
        # Fresh noise per call, NOT one fixed permutation reused everywhere: a
        # single draw makes its own deviation a systematic bias across every
        # evaluation, and the CI over those correlated runs comes out absurdly
        # narrow. Measured with a fixed draw: 51.7% [50.9, 52.6] — flagged as
        # broken when the only thing broken was the anchor.
        "--- azar": lambda seeds: rng.random(len(V)),
        "--- solo VBS": lambda seeds: zq,
        "centroide (viejo)": centroid,
        "centroide + calidad": lambda seeds: _z(centroid(seeds)) + 1.5 * zq,
        "fusion": fusion,
        "fusion + calidad": lambda seeds: fusion(seeds) + 1.5 * zq,
    }


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sizes", default="2,3", help="group sizes, comma separated")
    ap.add_argument("--splits", type=int, default=5)
    ap.add_argument("--per-size", type=int, default=25)
    ap.add_argument("--universe", choices=["tail", "full"], default="tail",
                    help="tail = under 50k IMDb votes (kills the popularity bias, but "
                         "leaves small profiles with nothing evaluable); full = everything")
    args = ap.parse_args()

    async with AsyncSessionLocal() as db:
        qdrant = QdrantService()
        ids, pos, V, vbs, pop, profiles = await _load(db, qdrant)
        names = sorted(profiles)
        universe = (pop < 50_000) if args.universe == "tail" else np.ones(len(V), bool)
        logger.info(f"{len(V)} films · {len(names)} profiles · universe={args.universe} "
                    f"({int(universe.sum())} films)")

        rng = np.random.default_rng(12345)
        scorers = build_scorers(V, vbs, pop, rng)
        results = {k: [] for k in scorers}
        per_user = {n: {k: [] for k in scorers} for n in names}
        picker = random.Random(999)

        for size in [int(s) for s in args.sizes.split(",")]:
            combos = list(itertools.combinations(names, size))
            picker.shuffle(combos)
            for group in combos[:args.per_size]:
                for split in range(args.splits):
                    seeds, tests = [], []
                    for n in group:
                        v = list(profiles[n])
                        random.Random(9000 + split).shuffle(v)
                        half = len(v) // 2
                        seeds.append(np.array(v[:half]))
                        tests.append((n, np.array(v[half:])))
                    seen = np.zeros(len(V), bool)
                    for s in seeds:
                        seen[s] = True
                    mask = universe & ~seen
                    evaluable = [(n, np.array([t for t in x if mask[t]])) for n, x in tests]
                    evaluable = [(n, t) for n, t in evaluable if len(t) >= MIN_TEST]
                    if len(evaluable) < min(2, size):
                        continue
                    total = int(mask.sum())
                    for label, fn in scorers.items():
                        score = fn(seeds)
                        order = np.argsort(-np.where(mask, score, -np.inf))
                        rank = np.empty(len(V), int)
                        rank[order] = np.arange(len(V))
                        pcts = []
                        for n, t in evaluable:
                            p = float(np.median(rank[t])) / total * 100
                            per_user[n][label].append(p)
                            pcts.append(p)
                        results[label].append(np.mean(pcts))

        n_eval = len(results["--- azar"])
        if not n_eval:
            logger.error("No evaluable groups — try --universe full or a smaller --sizes")
            return 1

        print(f"\n=== {n_eval} evaluaciones pareadas · percentil medio (menor = mejor) ===")
        for label in scorers:
            m, lo, hi = ci95(results[label])
            print(f"{label:24s} {m:6.1f}% [{lo:5.1f}, {hi:5.1f}]")
        # Judge the anchor by its INTERVAL, not its point estimate: with few
        # evaluations a random ranking lands anywhere, and calling that "broken"
        # would cry wolf. It is only suspicious when 50 falls outside the CI.
        anchor, a_lo, a_hi = ci95(results["--- azar"])
        if a_lo <= 50 <= a_hi:
            verdict = "OK"
        else:
            verdict = "SOSPECHOSO — la metrica no esta calibrada, no leas nada mas"
        print(f"\nancla de cordura: el azar da {anchor:.1f}% [{a_lo:.1f}, {a_hi:.1f}] "
              f"(deberia contener 50) -> {verdict}")
        if n_eval < 30:
            print(f"AVISO: solo {n_eval} evaluaciones. Los intervalos van a ser inutiles; "
                  f"sube --per-size/--splits o prueba --universe full, que deja participar "
                  f"a los perfiles pequenos.")

        base = "centroide (viejo)"
        print(f"\n=== contrastes pareados contra «{base}» (negativo = mejor) ===")
        b = np.array(results[base])
        for label in scorers:
            if label == base or label.startswith("---"):
                continue
            d = np.array(results[label]) - b
            m, lo, hi = ci95(d)
            wins = float(np.mean(d < 0) * 100)
            tag = "REAL" if hi < 0 else ("REAL(inverso)" if lo > 0 else "NO CONCLUYENTE")
            print(f"{label:24s} {m:+6.1f}pt [{lo:+6.1f}, {hi:+6.1f}]  gana en {wins:3.0f}%  {tag}")

        print(f"\n=== por usuario (una media agregada esconde de quien habla) ===")
        print(f"{'perfil':16s} {'n':>5s} {'viejo':>8s} {'mejor':>8s}")
        best = min((k for k in scorers if not k.startswith("---")),
                   key=lambda k: np.mean(results[k]))
        for n in names:
            a, c = per_user[n][base], per_user[n][best]
            if len(a) < 3:
                print(f"{n:16s} {len(a):5d}  (muestra insuficiente)")
                continue
            print(f"{n:16s} {len(a):5d} {np.mean(a):7.1f}% {np.mean(c):7.1f}%")
        print(f"(«mejor» = {best})")

        print(f"\n=== suelo de ruido: mismo metodo, solo cambia la particion ===")
        group = tuple(names[:min(3, len(names))])
        for label in (base, best):
            vals = []
            for split in range(10):
                seeds, tests = [], []
                for n in group:
                    v = list(profiles[n])
                    random.Random(500 + split).shuffle(v)
                    half = len(v) // 2
                    seeds.append(np.array(v[:half]))
                    tests.append(np.array(v[half:]))
                seen = np.zeros(len(V), bool)
                for s in seeds:
                    seen[s] = True
                mask = universe & ~seen
                ev = [np.array([t for t in x if mask[t]]) for x in tests]
                ev = [t for t in ev if len(t) >= MIN_TEST]
                if len(ev) < 2:
                    continue
                score = scorers[label](seeds)
                order = np.argsort(-np.where(mask, score, -np.inf))
                rank = np.empty(len(V), int)
                rank[order] = np.arange(len(V))
                vals.append(np.mean([float(np.median(rank[t])) / int(mask.sum()) * 100 for t in ev]))
            if vals:
                print(f"{label:24s} min {min(vals):5.1f}%  max {max(vals):5.1f}%  "
                      f"rango {max(vals)-min(vals):4.1f}pt")
        print("\nNada por debajo de ese rango es un hallazgo. Y mira la lista antes de creerte el numero.")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
