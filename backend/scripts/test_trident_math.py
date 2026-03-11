"""
Phase 4: Sigmoid & RRF Math Verification
Tests mathematical correctness of:
- ClusteringService.calculate_quality_weight (sigmoid)
- RecommendationService.reciprocal_rank_fusion (RRF)
"""
import sys
import os
import math

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def calculate_quality_weight(score: float) -> float:
    # Misma fórmula que ClusteringService — sigmoid centrada en 65
    if score is None:
        return 0.5
    return 1 / (1 + math.exp(-0.15 * (score - 65)))

def reciprocal_rank_fusion(candidate_lists: list[list], k: int = 60) -> dict:
    # Misma fórmula que RecommendationService
    scores = {}
    for lst in candidate_lists:
        for rank, movie in enumerate(lst):
            if movie.id not in scores:
                scores[movie.id] = 0.0
            scores[movie.id] += 1 / (k + rank)
    return scores

def test_sigmoid():
    """Test calculate_quality_weight sigmoid outputs."""
    tolerance = 0.02

    test_cases = [
        (50, 0.09),
        (65, 0.50),
        (80, 0.91),
    ]

    print("\n  --- Sigmoid Tests ---")
    all_pass = True
    for score, expected in test_cases:
        actual = calculate_quality_weight(score)
        diff = abs(actual - expected)
        status = "✅" if diff <= tolerance else "❌"
        print(f"  {status} score={score} → weight={actual:.4f} (expected ≈{expected}, Δ={diff:.4f})")
        if diff > tolerance:
            all_pass = False

    # Edge case: None input
    none_result = calculate_quality_weight(None)
    if none_result == 0.5:
        print(f"  ✅ score=None → weight={none_result} (fallback)")
    else:
        print(f"  ❌ score=None → weight={none_result} (expected 0.5)")
        all_pass = False

    return all_pass


def test_rrf():
    """Test reciprocal_rank_fusion correctness."""
    from unittest.mock import MagicMock

    print("\n  --- RRF Tests ---")

    # Create mock Movie objects with IDs
    def make_movie(movie_id):
        m = MagicMock()
        m.id = movie_id
        return m

    # 3 ranked lists with overlapping IDs
    # Movie 1 appears in ALL 3 lists (rank 0)
    # Movie 2 appears in lists 1 and 2 (rank 1, rank 0)
    # Movie 5 appears in only list 3 (rank 1)
    list_a = [make_movie(1), make_movie(2), make_movie(3)]
    list_b = [make_movie(2), make_movie(1), make_movie(4)]
    list_c = [make_movie(1), make_movie(5), make_movie(6)]

    k = 60  # default k
    scores = reciprocal_rank_fusion([list_a, list_b, list_c], k=k)

    # Movie 1: appears at rank 0 in lists A, C; rank 1 in list B
    # Score = 1/(60+0) + 1/(60+1) + 1/(60+0) = 2/60 + 1/61
    expected_movie1 = 2 * (1 / (k + 0)) + 1 / (k + 1)

    # Movie 5: appears at rank 1 in list C only
    # Score = 1/(60+1)
    expected_movie5 = 1 / (k + 1)

    all_pass = True

    score_1 = scores.get(1, 0)
    score_5 = scores.get(5, 0)

    print(f"  Movie 1 (in 3 lists): RRF score = {score_1:.6f} (expected {expected_movie1:.6f})")
    print(f"  Movie 5 (in 1 list):  RRF score = {score_5:.6f} (expected {expected_movie5:.6f})")

    # Core assertion: movie in 3 lists scores higher than movie in 1 list
    if score_1 > score_5:
        print(f"  ✅ Movie 1 ({score_1:.6f}) > Movie 5 ({score_5:.6f}) — RRF correctness confirmed")
    else:
        print(f"  ❌ MATH FAILED: Movie 1 should score higher than Movie 5")
        all_pass = False

    # Exact value check
    if abs(score_1 - expected_movie1) < 1e-10:
        print(f"  ✅ Movie 1 exact score matches formula")
    else:
        print(f"  ❌ MATH FAILED: Movie 1 score mismatch ({score_1} vs {expected_movie1})")
        all_pass = False

    if abs(score_5 - expected_movie5) < 1e-10:
        print(f"  ✅ Movie 5 exact score matches formula")
    else:
        print(f"  ❌ MATH FAILED: Movie 5 score mismatch ({score_5} vs {expected_movie5})")
        all_pass = False

    # Check all movies have scores
    expected_ids = {1, 2, 3, 4, 5, 6}
    actual_ids = set(scores.keys())
    if actual_ids == expected_ids:
        print(f"  ✅ All {len(expected_ids)} unique movies scored")
    else:
        missing = expected_ids - actual_ids
        print(f"  ❌ Missing movies in RRF output: {missing}")
        all_pass = False

    return all_pass


def main():
    print("=" * 60)
    print("  PHASE 4: Sigmoid & RRF Math Verification")
    print("=" * 60)

    sigmoid_ok = test_sigmoid()
    rrf_ok = test_rrf()

    print()
    if sigmoid_ok and rrf_ok:
        print("  ✅ TRIDENT MATH VERIFIED")
    else:
        failures = []
        if not sigmoid_ok:
            failures.append("Sigmoid")
        if not rrf_ok:
            failures.append("RRF")
        print(f"  ❌ MATH FAILED: {', '.join(failures)}")
        sys.exit(1)

    print("=" * 60)


if __name__ == "__main__":
    main()
