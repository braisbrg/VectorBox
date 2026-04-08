def normalize_similarity_score(score: float) -> float:
    """
    Map a raw Qdrant cosine similarity score to the 60–99 display scale.

    Ranges:
      score > 0.7  → 90–99  (strong match)
      0.2–0.7      → 60–90  (linear interpolation)
      score < 0.2  → 60     (floored)
    """
    MIN_SIM = 0.2
    MAX_SIM = 0.7

    if score > MAX_SIM:
        return min(99.0, 90.0 + (score - MAX_SIM) * 100)

    normalized = max(0.0, min(1.0, (score - MIN_SIM) / (MAX_SIM - MIN_SIM)))
    return 60.0 + normalized * 30.0
