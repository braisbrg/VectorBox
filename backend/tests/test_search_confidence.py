"""The confidence gate — 2026-07-29.

The engine used to fill the shelf whatever it found: twenty films whether the
nearest neighbour scored 0.65 or 0.19. "receta de tortilla de patatas" answered
with Ratatouille and "342342 8888 ????" with Werckmeister Harmonies — confident,
and nonsense as recommendations.

The threshold is measured, not chosen: scripts/experiment_confidence.py runs an
18-query panel three times through the real pipeline. These tests pin the
properties that experiment established, so a future tweak has to re-earn them.
"""
import pytest

from services.magic_search_ranking import (
    CONFIDENCE_SAMPLE,
    LOW_CONFIDENCE_MEAN,
    is_low_confidence,
    search_confidence,
)


def test_threshold_sits_inside_the_measured_margin():
    """Answerable never fell below 0.443; unanswerable never rose above 0.425."""
    assert 0.425 <= LOW_CONFIDENCE_MEAN <= 0.443


def test_threshold_leans_towards_answering():
    """Refusing a real question is a worse failure than answering a silly one, so
    the gate sits in the lower half of the measured margin."""
    margin_mid = (0.425 + 0.443) / 2
    assert LOW_CONFIDENCE_MEAN <= margin_mid


@pytest.mark.parametrize("cosines,expected", [
    ([0.65, 0.63, 0.62, 0.61, 0.60], False),   # "grief" territory
    ([0.53, 0.51, 0.50, 0.49, 0.48], False),   # "loneliness in a big city"
    ([0.29, 0.27, 0.26, 0.25, 0.24], True),    # "algo bueno"
    ([0.19, 0.18, 0.17, 0.16, 0.15], True),    # "342342 8888 ????"
    ([0.28, 0.25, 0.23, 0.22, 0.21], True),    # "receta de tortilla de patatas"
])
def test_measured_queries_land_on_the_right_side(cosines, expected):
    assert is_low_confidence(cosines) is expected


def test_confidence_reads_the_top_neighbours_not_the_tail():
    """A long tail of weak matches must not sink a query with strong ones."""
    strong_head = [0.7] * CONFIDENCE_SAMPLE + [0.01] * 50
    assert not is_low_confidence(strong_head)
    assert search_confidence(strong_head) == pytest.approx(0.7)


def test_empty_result_set_is_low_confidence_not_a_crash():
    assert search_confidence([]) == 0.0
    assert is_low_confidence([]) is True


def test_quality_is_not_used_as_a_confidence_signal():
    """The experiment killed the obvious alternative and that must stay killed.

    vbs_mean separated the two groups by -23.3: nonsense returns ACCLAIMED films
    (Werckmeister Harmonies at VBS 72 for "342342 8888 ????"). Ranking by quality
    when similarity is low would dress gibberish in prestige and look deliberate.
    """
    import inspect
    from services import magic_search_ranking

    src = inspect.getsource(magic_search_ranking.search_confidence)
    assert "vbs" not in src.lower(), (
        "confidence must be measured on similarity alone — quality does not "
        "discriminate answerable from unanswerable (measured margin -23.3)"
    )


# ── the three shapes a low-confidence query can actually take ────────────────
# Measured 2026-07-29. Confidence alone was refusing all of them, and only one
# deserved it.
def test_a_quality_only_request_counts_as_descriptive():
    """'peliculas muy bien valoradas' has one criterion and it is answerable.

    min_vectorbox_score was missing from has_descriptive_filters, so the gate
    refused a query the catalogue answers perfectly.
    """
    from services.magic_search_ranking import has_descriptive_filters
    from services.nlp_search import MovieSearchIntent

    intent = MovieSearchIntent(semantic_query="x", reasoning="r", min_vectorbox_score=75)
    assert has_descriptive_filters(intent)


def test_quality_words_get_a_filter_even_when_the_parser_forgets():
    """Third field to need a rule instead of a prompt.

    First the parser set min_rating=8.0 (top ~2% of TMDB, three results); after
    the description was tightened it set nothing at all and the query returned
    zero. The guard makes it deterministic either way.
    """
    from services.nlp_search import QUALITY_REQUEST_MIN_VBS, ensure_quality_filter, MovieSearchIntent

    for q in ["peliculas muy bien valoradas", "lo mejor del catalogo",
              "critically acclaimed films", "must-see masterpieces"]:
        out = ensure_quality_filter(MovieSearchIntent(semantic_query="x", reasoning="r"), q)
        assert out.min_vectorbox_score == QUALITY_REQUEST_MIN_VBS, q


def test_the_guard_never_overrides_a_filter_the_parser_did_set():
    from services.nlp_search import ensure_quality_filter, MovieSearchIntent

    out = ensure_quality_filter(
        MovieSearchIntent(semantic_query="x", reasoning="r", min_metacritic=85),
        "algo muy aclamado por la critica",
    )
    assert out.min_vectorbox_score is None
    assert out.min_metacritic == 85


def test_a_thematic_query_gets_no_quality_filter():
    """The guard must not fire on queries that never mentioned quality."""
    from services.nlp_search import ensure_quality_filter, MovieSearchIntent

    out = ensure_quality_filter(
        MovieSearchIntent(semantic_query="x", reasoning="r"),
        "algo lento y triste sobre el duelo, sin sustos",
    )
    assert out.min_vectorbox_score is None


def test_open_request_is_a_field_because_confidence_cannot_tell():
    """'no se que ver' scored 0.306 and gibberish 0.23 — inside the noise.

    A person who does not know what to watch cannot be asked to be more
    specific, so refusing them is the one failure with no recovery. Only the
    parser can distinguish it, so it says so explicitly.
    """
    from services.nlp_search import MovieSearchIntent

    assert "open_request" in MovieSearchIntent.model_fields
    assert MovieSearchIntent.model_fields["open_request"].default is False
