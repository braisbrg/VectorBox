"""Group sync: fuse one ranked list per member instead of averaging their vectors.

Why not the centroid it replaces: averaging 30-50 vectors lands the query next to
the catalogue's own centre, and that neighbourhood is a few hundred hub films —
397 of them filled the top-20 of 300 different simulated tastes, and five real
groups shared 12.8 of their 20 recommendations, including a group the requester
wasn't in. Measured 2026-08-03; the full ledger (and the rejected alternatives)
lives in BACKLOG.md under "GRUPOS: fusionar listas, no promediar vectores".

Effect, paired against the same groups/splits with 95% CI: -10.1pt [-12.6, -7.7]
on the median rank of held-out loved films, real for all 12 measured profiles
individually, and it *grows* with group size (the average of more people is a
worse summary; more lists to fuse is a better one).

Everything here is pure: lists in, ranked ids out. The caller owns Redis, Qdrant
and Postgres. That is what makes it testable without infrastructure.
"""
from typing import Dict, Iterable, List, Optional, Sequence

# Fusion weights. knn and the member centroid carry the signal; the structured
# lists sharpen the head of the list (director alone quadrupled rec@1%). Equal
# weights measured 2.6pt WORSE — piling every list in at 1.0 dilutes knn.
LIST_WEIGHTS = {"knn": 1.0, "cent": 1.0, "dir": 0.5, "keyw": 0.3, "wl": 0.3}

RRF_K = 60          # standard; K=10 measured worse
QUALITY_WEIGHT = 1.5  # z(VBS) units. 1.0 scores marginally better, 1.5 keeps
                      # VBS-45 films (Warcraft) out of the head. Curve is the
                      # same shape at every group size, so one constant is enough.
MIN_VBS = 40        # 55 (the feed's constant) cuts 6.7-32.6% of users' loved films
MIN_RUNTIME = 40    # no Ghibli museum shorts in a "what do we watch tonight" list
MAX_PER_DIRECTOR = 2  # without it one auteur took 5 of the top 6


def _rrf_from_ranked(ids: Sequence[int], weight: float, out: Dict[int, float]) -> None:
    """Accumulate reciprocal-rank contributions for one already-ranked list."""
    for rank, mid in enumerate(ids):
        out[mid] = out.get(mid, 0.0) + weight / (RRF_K + rank + 1)


def _zscore(values: Dict[int, float]) -> Dict[int, float]:
    if not values:
        return {}
    xs = list(values.values())
    mean = sum(xs) / len(xs)
    var = sum((x - mean) ** 2 for x in xs) / len(xs)
    sd = var ** 0.5 or 1.0
    return {k: (v - mean) / sd for k, v in values.items()}


def member_lists(
    neighbours: Dict[int, List[int]],
    loved: Iterable[int],
    directors: Optional[List[int]] = None,
    keywords: Optional[List[int]] = None,
    watchlist: Optional[Iterable[int]] = None,
) -> Dict[str, List[int]]:
    """Turn one member's data into their ranked lists.

    `neighbours` maps each of their loved films to its precomputed top-K
    neighbours (centred kNN, built by scripts/build_neighbor_table.py). A film
    scores by how many of your loved films reached it and how early — item-based
    kNN, the same shape Signal A already uses for the feed.

    `directors`/`keywords` are candidate ids already filtered to "shares an
    attribute this member loves at least twice"; they arrive ordered by strength.
    """
    votes: Dict[int, float] = {}
    for seed in loved:
        for rank, mid in enumerate(neighbours.get(seed, ())):
            # earlier neighbours weigh more; a film reached by many seeds wins
            votes[mid] = votes.get(mid, 0.0) + 1.0 / (rank + 1)
    knn = sorted(votes, key=lambda m: -votes[m])

    lists = {"knn": knn}
    if directors:
        lists["dir"] = list(directors)
    if keywords:
        lists["keyw"] = list(keywords)
    if watchlist:
        lists["wl"] = list(watchlist)
    return lists


def fuse(
    per_member: Sequence[Dict[str, List[int]]],
    quality: Dict[int, float],
    runtime: Dict[int, float],
    directors_of: Dict[int, List[str]],
    exclude: Optional[set] = None,
    limit: int = 50,
) -> List[int]:
    """Fuse every member's lists into one ranked list of tmdb_ids.

    `quality` is VBS per tmdb_id (0-100). Films below MIN_VBS or MIN_RUNTIME are
    dropped: neither floor moves the ranking metric (+0.8pt, not significant),
    both keep visible junk out — a judgement, deliberately, not a measurement.
    """
    exclude = exclude or set()
    acc: Dict[int, float] = {}
    for lists in per_member:
        for name, ids in lists.items():
            weight = LIST_WEIGHTS.get(name)
            if weight:
                _rrf_from_ranked([i for i in ids if i not in exclude], weight, acc)
    if not acc:
        return []

    zq_all = _zscore({m: min(max(quality.get(m, 0.0), 0.0), 100.0) for m in acc})
    ranked = _zscore(acc)
    scored = {m: ranked[m] + QUALITY_WEIGHT * zq_all[m] for m in acc}

    out: List[int] = []
    used: Dict[str, int] = {}
    for mid in sorted(scored, key=lambda m: -scored[m]):
        if quality.get(mid, 0.0) < MIN_VBS or runtime.get(mid, 0.0) < MIN_RUNTIME:
            continue
        names = directors_of.get(mid) or ["?"]
        if any(used.get(d, 0) >= MAX_PER_DIRECTOR for d in names):
            continue
        out.append(mid)
        for d in names:
            used[d] = used.get(d, 0) + 1
        if len(out) >= limit:
            break
    return out


# Deliberately NOT here: a per-member "who put this film in the list" share.
# It is the honest attribution — the reciprocal-rank mass, not a cosine measured
# afterwards — but group-vibe-picker.tsx reads `contributors[].score` as a
# similarity for its predict column, its sort, and its agreement indicator
# (`1 - (max - min)`, which collapses to ~0 for a normalised share). It needs the
# frontend in the same change, so the service keeps returning cosines for now.
