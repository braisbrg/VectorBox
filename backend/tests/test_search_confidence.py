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
