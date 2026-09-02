"""Pin every branch of `services/magic_search_ranking.py`.

These tests cover the scoring + filtering decisions Magic Search makes
*after* Qdrant returns candidates: the post-filter that drops rows failing
intent dimensions, the title-boost eligibility gate (the one that protects
us from "Deprisa, deprisa → Fast and Furious"), the quality-gate weight
including the NULL-VBS branch, the blended-score formula, and the
auto-deep-analysis trigger.

Every helper is pure / synchronous — no DB, no Qdrant, no HTTP. The tests
construct minimal `MovieSearchIntent` objects and `Movie` instances
in-memory and assert specific behaviours.
"""
import pytest
from types import SimpleNamespace

from services.magic_search_ranking import (
    DEEP_ANALYSIS_COMPLEXITY_THRESHOLD,
    TITLE_BOOST_MIN_SIM,
    compute_relevance,
    has_descriptive_filters,
    intent_complexity,
    movie_passes_post_filter,
    quality_gate_weight,
    should_run_deep_analysis,
    trim_to_relevant,
    title_boost_eligible,
    title_sim_score,
)
from services.nlp_search import MovieSearchIntent


def _intent(**overrides) -> MovieSearchIntent:
    """Minimal MovieSearchIntent with everything else at default."""
    base = dict(semantic_query="x", reasoning="test")
    base.update(overrides)
    return MovieSearchIntent(**base)


def _movie(**overrides):
    """Lightweight Movie stand-in — SimpleNamespace avoids the SQLAlchemy
    instantiation cost while still satisfying attribute access."""
    base = dict(
        is_adult=False, is_upcoming=False, mpaa_rating=None, oscar_wins=0,
        omdb_countries=None, omdb_languages=None, awards_text=None,
        vectorbox_score=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ============================================================================
# intent_complexity
# ============================================================================


def test_intent_complexity_empty_is_zero():
    assert intent_complexity(_intent()) == 0


def test_intent_complexity_counts_each_populated_field():
    intent = _intent(
        include_genres=["Drama"], year_min=1990, countries=["France"],
        min_oscar_wins=1,
    )
    assert intent_complexity(intent) == 4


def test_intent_complexity_popularity_vibe_counts_when_not_any():
    assert intent_complexity(_intent(popularity_vibe="hidden_gem")) == 1
    assert intent_complexity(_intent(popularity_vibe="any")) == 0


def test_intent_complexity_reference_movie_counts():
    """`reference_movie='X'` is a meaningful constraint — count it."""
    assert intent_complexity(_intent(reference_movie="The Godfather")) == 1


# ============================================================================
# title_boost_eligible
# ============================================================================


def test_title_boost_rejected_when_reference_movie_set():
    intent = _intent(reference_movie="Inception")
    assert title_boost_eligible(intent, "Inception") is False


def test_title_boost_rejected_when_descriptive_filters_present():
    intent = _intent(include_genres=["Thriller"])
    assert title_boost_eligible(intent, "Inception") is False


def test_title_boost_rejected_when_query_is_long():
    """40-char limit — long natural-language queries are never title lookups."""
    long_query = "películas que sean sobre venganza y nostalgia"
    assert len(long_query) > 40
    assert title_boost_eligible(_intent(), long_query) is False


def test_title_boost_accepted_for_short_unfiltered_query():
    assert title_boost_eligible(_intent(), "Inception") is True
    assert title_boost_eligible(_intent(), "The Godfather") is True


def test_title_boost_rejected_for_any_sprint2_filter():
    for kw in (
        {"countries": ["France"]},
        {"spoken_languages": ["Galician"]},
        {"awards_contains": ["BAFTA"]},
    ):
        assert title_boost_eligible(_intent(**kw), "Anything") is False


# ============================================================================
# title_sim_score / has_descriptive_filters
# ============================================================================


def test_title_sim_score_handles_case_and_punctuation():
    assert title_sim_score("Inception", "Inception") == 1.0
    assert title_sim_score("inception", "Inception") == 1.0
    assert title_sim_score("Godfather", "The Godfather") > 0.5


def test_has_descriptive_filters_returns_false_on_empty_intent():
    assert has_descriptive_filters(_intent()) is False


def test_has_descriptive_filters_recognises_each_dimension():
    assert has_descriptive_filters(_intent(year_min=1990)) is True
    assert has_descriptive_filters(_intent(include_genres=["Drama"])) is True
    assert has_descriptive_filters(_intent(countries=["France"])) is True
    assert has_descriptive_filters(_intent(spoken_languages=["Galician"])) is True
    assert has_descriptive_filters(_intent(awards_contains=["BAFTA"])) is True


# ============================================================================
# quality_gate_weight
# ============================================================================


def test_quality_gate_floor_when_vbs_is_zero():
    """Sigmoid is essentially 0 far below midpoint → weight ≈ floor."""
    w = quality_gate_weight(vbs=0, quality_gate_bypass=False)
    assert 0.20 <= w <= 0.22


def test_quality_gate_handles_null_vbs_as_zero():
    """The 2026-05-15 bugfix: NULL VBS films must NOT bypass the gate."""
    w_null = quality_gate_weight(vbs=None, quality_gate_bypass=False)
    w_zero = quality_gate_weight(vbs=0, quality_gate_bypass=False)
    assert abs(w_null - w_zero) < 1e-6


def test_quality_gate_high_vbs_approaches_one():
    """VBS=95 (canonical masterpiece) should clear the gate cleanly."""
    w = quality_gate_weight(vbs=95, quality_gate_bypass=False)
    assert 0.95 < w <= 1.0


def test_quality_gate_bypass_softens_midpoint():
    """quality_gate_bypass=True shifts midpoint 55 -> 25, so a VBS=35 film
    that would be penalised in default mode now sits near the upper plateau."""
    w_default = quality_gate_weight(vbs=35, quality_gate_bypass=False)
    w_bypass = quality_gate_weight(vbs=35, quality_gate_bypass=True)
    assert w_bypass > w_default


# ============================================================================
# compute_relevance
# ============================================================================


def test_relevance_applies_title_boost_when_eligible():
    """Short query + matching title + no filters → boost fires."""
    rel_with, ts, _ = compute_relevance(
        raw_cosine=0.55, query="Inception", intent=_intent(),
        title="Inception", vbs=80,
    )
    rel_without, ts2, _ = compute_relevance(
        raw_cosine=0.55, query="Inception", intent=_intent(),
        title="Some Other Movie", vbs=80,
    )
    assert ts == 1.0
    assert ts2 is not None and ts2 < TITLE_BOOST_MIN_SIM
    assert rel_with > rel_without


def test_relevance_no_title_boost_when_reference_movie_set():
    """`reference_movie=X` means "like X" — title-boost must NOT fire even
    if a candidate's title happens to match the query."""
    _rel, ts, _ = compute_relevance(
        raw_cosine=0.55, query="Inception",
        intent=_intent(reference_movie="Inception"),
        title="Inception", vbs=80,
    )
    assert ts is None  # gate refused to compute → confirms not eligible


def test_quality_decides_the_order_and_nothing_else():
    """Two facts that have to hold together, on identical inputs.

    Ordering: the pattern that hit user 212's 'Deprisa, deprisa' search —
    NULL-VBS films must not outrank VBS-populated ones.

    Display: relevance must NOT move with VBS. The route used to receive
    relevance × weight and divide the weight back out to show it; if the
    product ever comes back into the return value, the first assert fails.
    """
    rel_null, _, w_null = compute_relevance(
        raw_cosine=0.55, query="cine social",
        intent=_intent(include_genres=["Drama"]),  # descriptive → no title boost
        title="Random Film", vbs=None,
    )
    rel_hi, _, w_hi = compute_relevance(
        raw_cosine=0.55, query="cine social",
        intent=_intent(include_genres=["Drama"]),
        title="Random Film", vbs=85,
    )
    assert rel_null == rel_hi          # same match → same number on screen
    assert w_null < 0.25
    assert w_hi > 0.90
    assert rel_hi * w_hi > rel_null * w_null   # …but quality still ranks


# ============================================================================
# movie_passes_post_filter
# ============================================================================


def test_post_filter_passes_when_intent_empty():
    assert movie_passes_post_filter(_movie(), _intent()) is True


def test_post_filter_rejects_adult_when_safe_mode_default():
    """`safe_mode=True` (default) drops `is_adult=True` rows."""
    assert movie_passes_post_filter(_movie(is_adult=True), _intent()) is False
    assert movie_passes_post_filter(_movie(is_adult=True), _intent(safe_mode=False)) is True


def test_post_filter_rejects_unreleased():
    """An unreleased film is not a recommendation, whatever the intent says."""
    assert movie_passes_post_filter(_movie(is_upcoming=True), _intent()) is False
    assert movie_passes_post_filter(_movie(is_upcoming=True),
                                    _intent(safe_mode=False)) is False


def test_post_filter_enforces_mpaa_allowlist():
    family = _intent(mpaa_ratings=["G", "PG"])
    assert movie_passes_post_filter(_movie(mpaa_rating="PG"), family) is True
    assert movie_passes_post_filter(_movie(mpaa_rating="R"), family) is False
    assert movie_passes_post_filter(_movie(mpaa_rating=None), family) is False


def test_post_filter_oscar_threshold():
    won3 = _intent(min_oscar_wins=3)
    assert movie_passes_post_filter(_movie(oscar_wins=5), won3) is True
    assert movie_passes_post_filter(_movie(oscar_wins=2), won3) is False
    assert movie_passes_post_filter(_movie(oscar_wins=None), won3) is False


def test_post_filter_country_or_within_list():
    """A film must match AT LEAST ONE of the requested countries."""
    eu = _intent(countries=["France", "Italy"])
    assert movie_passes_post_filter(_movie(omdb_countries=["France"]), eu) is True
    assert movie_passes_post_filter(_movie(omdb_countries=["Italy", "USA"]), eu) is True
    assert movie_passes_post_filter(_movie(omdb_countries=["South Korea"]), eu) is False
    assert movie_passes_post_filter(_movie(omdb_countries=None), eu) is False


def test_post_filter_language_or_within_list():
    galego = _intent(spoken_languages=["Galician"])
    assert movie_passes_post_filter(_movie(omdb_languages=["Galician", "Spanish"]), galego) is True
    assert movie_passes_post_filter(_movie(omdb_languages=["Spanish"]), galego) is False


def test_post_filter_awards_contains_all_needles():
    """`awards_contains` is AND-combined — every substring must appear."""
    intent = _intent(awards_contains=["BAFTA", "Cannes"])
    assert movie_passes_post_filter(
        _movie(awards_text="Won 1 BAFTA. Nominated at Cannes Film Festival"),
        intent,
    ) is True
    assert movie_passes_post_filter(
        _movie(awards_text="Won 1 BAFTA"),
        intent,
    ) is False
    assert movie_passes_post_filter(
        _movie(awards_text=None),
        intent,
    ) is False


# ============================================================================
# should_run_deep_analysis
# ============================================================================


def test_deep_analysis_user_requested_always_wins():
    assert should_run_deep_analysis(_intent(), user_requested=True) is True


def test_deep_analysis_low_complexity_intent_skips():
    assert should_run_deep_analysis(_intent(include_genres=["Drama"])) is False


def test_deep_analysis_auto_triggers_at_threshold():
    """3 filters set = exactly at threshold → auto-deep."""
    intent = _intent(
        include_genres=["Drama"], year_min=1990, countries=["France"],
    )
    assert intent_complexity(intent) == DEEP_ANALYSIS_COMPLEXITY_THRESHOLD
    assert should_run_deep_analysis(intent) is True


def test_deep_analysis_just_below_threshold():
    intent = _intent(
        include_genres=["Drama"], year_min=1990,
    )
    assert intent_complexity(intent) == DEEP_ANALYSIS_COMPLEXITY_THRESHOLD - 1
    assert should_run_deep_analysis(intent) is False


# --- fetch width: a filter needs something to filter -------------------------
#
# The dimensions applied AFTER the vector search — in Postgres, because they are
# not in the Qdrant payload — can only ever keep a subset of what the fetch
# returned. Both places that do this were fetching too narrowly, and the symptom
# was silence: a short list looks like a narrow query, not like a bug.


def test_post_filtered_searches_fetch_wider_than_they_return():
    """Measured 2026-07-29: at a 20-candidate fetch, "thrillers coreanos" parsed
    correctly to countries=['South Korea'] and kept ONE film, while the catalogue
    holds 219 Korean ones. At 150 it returns Memories of Murder."""
    from services.magic_search_ranking import (
        SEARCH_FETCH_POST_FILTERED, SEARCH_RESULT_LIMIT,
    )

    assert SEARCH_FETCH_POST_FILTERED >= SEARCH_RESULT_LIMIT * 5


def test_similar_overfetches_past_its_quality_gate():
    """/similar drops everything below VBS 55 / 100 votes / no poster, and only
    38.9% of the catalogue clears that. Measured over 40 random films asking for
    12 similars: fetching 2x returned 8.6 on average with 12 of 40 shelves full;
    fetching 8x returned 11.9 with 39 of 40 full."""
    from routers.similar import QUALITY_GATE_OVERFETCH

    assert QUALITY_GATE_OVERFETCH >= 8


def test_rail_filters_reach_the_query_not_just_the_backstop():
    """`search_filters` is the QDRANT dict for the wide rows' vector search — only
    ever {"include_tmdb_ids": [...]} for providers, None otherwise. The rail's own
    constraints live in a different dict, and wiring the wrong one is a SILENT
    no-op: apply_rail_filters ignores include_tmdb_ids, the row builds unfiltered,
    and the post-filter cuts it exactly as before. Measured, not read.
    """
    from sqlalchemy import select
    from models.database import Movie
    from services.recommendation_engine import apply_rail_filters

    base = select(Movie)
    assert apply_rail_filters(base, None) is base
    assert apply_rail_filters(base, {"include_tmdb_ids": [1, 2, 3]}) is base, \
        "a provider id-set is not a rail filter and must not silently look applied"

    for key, value in (("min_vectorbox_score", 80), ("year_min", 1990),
                       ("year_max", 2010), ("max_runtime", 100),
                       ("include_genres", ["Drama"])):
        assert apply_rail_filters(base, {key: value}) is not base, key


# ── relevance cliff ──────────────────────────────────────────────────────────
#
# The rule that stops a filtered search padding its row with the least-bad
# content of a small box. Not a judgement about the query — three candidate
# query-level detectors were measured and all three misclassified a good case
# (see the comment on RELEVANCE_CLIFF), so this only ever trims the tail.

def _hit(score):
    return {"score": score, "metadata": {}}


def test_cliff_keeps_everything_when_the_row_is_flat():
    """A query the catalogue answers well has no cliff to cut at."""
    rows = [_hit(s) for s in (0.74, 0.73, 0.72, 0.70, 0.68, 0.65)]
    assert len(trim_to_relevant(rows)) == 6


def test_cliff_drops_the_padded_tail():
    """0.30 is 41% of 0.72 — not an answer to the same question."""
    rows = [_hit(s) for s in (0.72, 0.70, 0.68, 0.35, 0.32, 0.30)]
    kept = [r["score"] for r in trim_to_relevant(rows)]
    assert kept == [0.72, 0.70, 0.68]


def test_cliff_never_empties_a_non_empty_row():
    """The head always survives — the worst case is a SHORT row, never a blank
    page. This is the whole reason the cliff is relative and not a threshold."""
    for scores in ([0.9], [0.31, 0.30], [0.9, 0.1, 0.1], [0.4] * 20):
        rows = [_hit(s) for s in scores]
        assert len(trim_to_relevant(rows)) >= 1


def test_cliff_is_relative_not_absolute():
    """Same SHAPE at two different scales must keep the same films: a query
    whose best neighbour is 0.45 is not worse than one peaking at 0.90, it is
    phrased differently. An absolute floor was measured and it put kung fu 70s
    (0.477, a correct answer) below found footage 60s (0.557, padding)."""
    high = trim_to_relevant([_hit(s) for s in (0.90, 0.80, 0.60)])
    low = trim_to_relevant([_hit(s) for s in (0.45, 0.40, 0.30)])
    assert len(high) == len(low) == 2


def test_cliff_tolerates_empty_and_zero():
    assert trim_to_relevant([]) == []
    assert len(trim_to_relevant([_hit(0.0), _hit(0.0)])) == 2
