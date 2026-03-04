# 🧠 DATA SCIENCE DIRECTOR: Trident, RRF & Qdrant Logic

> **Role:** Data Science & ML Lead
> **Domain:** Recommendation Algorithms, Vector Search, Scoring Math
> **Last Updated:** 2026-03-04

This file contains all data science logic, mathematical formulas, and Qdrant configuration for the VectorBox recommendation engine.

---

## 1. The "Trident" Hybrid System

VectorBox generates recommendations using **three distinct engines** fused via Reciprocal Rank Fusion (RRF):

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Signal A      │    │ Signal Auteur   │    │   Signal C      │
│   VECTOR        │    │   DIRECTORS     │    │   HIDDEN GEMS   │
│   (Vibe)        │    │   (Directors)   │    │   (Score/Hype)  │
└────────┬────────┘    └────────┬────────┘    └────────┬────────┘
         │                      │                      │
         └──────────────────────┼──────────────────────┘
                                │
                         ┌──────▼──────┐
                         │     RRF     │
                         │   FUSION    │
                         └──────┬──────┘
                                │
                         ┌──────▼──────┐
                         │   FINAL     │
                         │   RANKING   │
                         └─────────────┘
```

### Signal A: Vector (Vibe)
- **Source:** Qdrant dense embeddings (384-dimensional)
- **Purpose:** Captures plot similarity, thematic "vibe", and genre alignment
- **Model:** `all-MiniLM-L6-v2` via Sentence-Transformers
- **Seeding:** Movies rated 4+ stars **OR** explicitly liked (`is_liked`)

### Signal Auteur (Directors)
- **Source:** User's high-rated history
- **Purpose:** Boosts movies by directors the user loves
- **Logic:** Explicit check: "Has this director made a movie the user rated 4+ stars?"
- **Note:** Renamed from "Signal B" in `recommendation_service.py` logs to avoid collision with Signal B (K-Means centroid) in `recommendation_engine.py`.

### Signal C: Hidden Gems
- **Source:** Score-to-Hype ratio filtering
- **Purpose:** Identifies critically acclaimed but underexposed films
- **Logic:** Uses **dynamic thresholds** based on user's movie count:

| Profile | Movies | Min Score | Max Popularity | Min Votes |
| :--- | :--- | :---: | :---: | :---: |
| Cold Start | < 30 | 60 | 40 | 200 |
| Growing | 30–99 | 65 | 30 | 300 |
| Rich | 100+ | 75 | 20 | 500 |

- **Implementation:** `_get_signal_c_thresholds()` in `recommendation_engine.py`

### Observability Logs (The `[TRIDENT]` Prefix)
- **Requirement:** All Trident signal execution times (A, Auteur, C) MUST be logged using the `[TRIDENT]` prefix to track performance of each individual engine phase.

### Reciprocal Rank Fusion (RRF)
```python
def rrf_score(ranks: list[int], k: int = 60) -> float:
    """Combine multiple ranking signals into a single score."""
    return sum(1 / (k + rank) for rank in ranks)
```

---

## 2. Scoring Formula

Every recommendation receives a **final score (0-100%)** derived from:

```python
FinalScore = Similarity (Cosine) * QualityWeight (Sigmoid)
```

### Cosine Similarity
- **Source:** Qdrant vector search
- **Range:** 0.0 - 1.0
- **Interpretation:** How semantically similar is this movie to the query/profile?

### Quality Weight (Sigmoid Curve)
A **non-linear boosting function** applied to the VectorBox Score:

```python
import math

def sigmoid_weight(score: float, midpoint: float = 65, steepness: float = 0.15) -> float:
    """
    Non-linear quality boost.
    
    Parameters:
        score: VectorBox Score (0-100)
        midpoint (x0): Score where weight = 0.5 (default: 65)
        steepness (k): How sharp the transition is (default: 0.15)
    
    Returns:
        Weight between 0.0 and 1.0
    """
    return 1 / (1 + math.exp(-steepness * (score - midpoint)))
```

### Visual Effect
| VectorBox Score | Sigmoid Weight | Effect |
| :---: | :---: | :--- |
| 50 | ~0.09 | Heavy penalty |
| 60 | ~0.33 | Moderate penalty |
| 65 | 0.50 | Neutral (midpoint) |
| 70 | ~0.67 | Moderate boost |
| 80 | ~0.91 | Strong boost |
| 90 | ~0.98 | Maximum boost |

> [!NOTE]
> This prevents "relevant trash" (high similarity but low quality) from appearing in recommendations.

---

## 3. VectorBox Score (Quality Metric)

### Definition
An aggregated quality score from multiple review sources:

| Source | Weight | Notes |
| :--- | :--- | :--- |
| **IMDb** | High | Normalized from 1-10 scale |
| **Metacritic** | Medium | Professional critics |
| **Rotten Tomatoes** | Medium | Critic + Audience |
| **Letterboxd** | High | Cinephile community |

### Scale
- **Range:** 0-100
- **Sweet Spot:** 65+ (sigmoid midpoint)
- **Quality Floor:** Dynamic (60–75+) for "Hidden Gems" section (depends on user profile size)

---

## 4. Diversity Algorithms

### MMR (Maximal Marginal Relevance)
Used in `clustering_service.mmr_rerank`:

```python
def mmr_score(relevance: float, max_similarity_to_selected: float, lambda_: float = 0.7) -> float:
    """
    Balance relevance vs diversity.
    
    Parameters:
        relevance: Original similarity score
        max_similarity_to_selected: How similar to already-selected items
        lambda_: Trade-off (0.7 = 70% relevance, 30% diversity)
    """
    return lambda_ * relevance - (1 - lambda_) * max_similarity_to_selected
```

**Effect:** Re-ranks top results to penalize items too similar to already-selected items.

### Collection Collapsing
**Problem:** Single franchise floods recommendations (e.g., Harry Potter 1, 2, 3, 4, 5...)

**Solution:** In `get_item_based_recommendations`:
1. Detect franchise members (same collection)
2. Keep only the highest-rated one as "Super Seed"
3. Collapse others from input set

---

## 5. Clustering Logic (K-Means)

### Configuration
| Parameter | Value | Notes |
| :--- | :--- | :--- |
| **Algorithm** | K-Means | Scikit-learn implementation |
| **Vectors** | 384-dimensional | `all-MiniLM-L6-v2` embeddings |
| **Optimal K** | `min(5, max(2, N // 20))` | Dynamic based on history size |

### Rating Weights
| User Rating | Weight | Interpretation |
| :---: | :---: | :--- |
| 4-5 stars | 1.0 | Full influence |
| 2-3.5 stars | 0.5 | Reduced influence |
| < 2 stars | 0.1 | Minimal (negative signal) |

### Recency Bias
When `use_recency_bias=True`:
- Recent ratings get higher influence
- Older ratings decay exponentially
- Purpose: Capture evolving taste

---

## 6. Qdrant Configuration

### Client & Schemas
> [!WARNING]
> Use `AsyncQdrantClient` exclusively. Synchronous client is forbidden.
> Use `QdrantPayload` (Pydantic) from `models/external_schemas.py` to validate and enforce schema on vector insert/upsert. Do not use raw dicts.

### Search API
```python
# ✅ CORRECT: Modern query_points API
results = await client.query_points(
    collection_name="movies",
    query=query_vector,
    limit=20,
    with_payload=True,
    query_filter=Filter(
        must=[
            FieldCondition(key="year", range=Range(gte=1990, lte=1999)),
            FieldCondition(key="genres", match=MatchAny(any=["Crime", "Drama"]))
        ]
    )
)

# ❌ DEPRECATED: Old search API
# results = await client.search(...)
```

### Payload Indexes
The following payload indexes enable fast filtering:

| Field | Type | Purpose |
| :--- | :--- | :--- |
| `vote_count` | Integer | Validity filtering |
| `vectorbox_score` | Float | Quality filtering |
| `popularity` | Float | Hype ceiling (Hidden Gems) |
| `year` | Integer | Year range queries |
| `genres` | Keyword | Genre matching |

### Index Creation
```bash
docker-compose exec backend python scripts/create_qdrant_indexes.py
```

---

## 7. Feed Section Logic

| Section | Algorithm | Key Parameters |
| :--- | :--- | :--- |
| **Because you watched [X]** | Item-Item CF | Content-only vector (ignores title) |
| **Your Taste ([Cluster])** | Centroid Search | User's taste cluster centroid |
| **Hidden Gems** | Score-to-Hype Filter | Dynamic: Cold `60/40/200`, Growing `65/30/300`, Rich `75/20/500` |
| **Deep Dive** | Super Seed | Weighted favorites |
| **Comfort Zone** | Anti-Recommendation | Non-overlapping genres |

---

*For backend implementation patterns, see [backend.md](backend.md).*
*For architectural rules, see [architect.md](../c-suite/architect.md).*
