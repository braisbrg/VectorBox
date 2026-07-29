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


# --- a degraded run is not an unanswerable question -------------------------
#
# Measured 2026-07-29 with scripts/audit_search.py: Groq's free tier caps at 8000
# tokens per MINUTE and one parse costs ~2000, so four searches in a row leave
# the sentence unread. An unparsed intent has no `open_request` and no
# `min_vectorbox_score`, so every gentle query fell through to the refusal and
# the user got an empty page — our outage, reported as their bad question.


def test_every_give_up_path_is_recognised_as_a_failed_parse():
    """The three ways nlp_search gives up, plus the router's own fallback.

    These are literal strings in four places; a reworded one would silently turn
    `degraded` off forever, and the only symptom would be users being told their
    question was unanswerable during a rate-limit spike.
    """
    from services.nlp_search import MovieSearchIntent, parse_failed

    for reasoning in (
        "No LLM available",
        "All models failed: RateLimitError(429)",
        "LLM unavailable: connection reset",
    ):
        assert parse_failed(MovieSearchIntent(semantic_query="q", reasoning=reasoning)), reasoning


def test_a_real_intent_is_not_a_failed_parse():
    from services.nlp_search import MovieSearchIntent, parse_failed

    assert not parse_failed(MovieSearchIntent(
        semantic_query="slow sad films about grief",
        reasoning="The user wants a contemplative drama about loss.",
    ))


@pytest.mark.asyncio
async def test_no_api_key_produces_an_intent_that_reports_itself_as_failed(monkeypatch):
    """End-to-end for the one give-up path reachable without a network call."""
    from services import nlp_search

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    intent = await nlp_search.parse_user_intent("algo lento y triste sobre el duelo")

    assert nlp_search.parse_failed(intent)
    # The raw query survives as the semantic query — the search still runs, it
    # just runs on the words instead of on their meaning.
    assert intent.semantic_query == "algo lento y triste sobre el duelo"


# --- a bar with no subject, and a filter with nothing to filter --------------


@pytest.mark.parametrize("kwargs", [
    {"min_vectorbox_score": 75},   # "peliculas muy bien valoradas"
    {"min_metacritic": 75},        # "algo muy aclamado por la critica"
    {"min_imdb_rating": 8.0},
    {"min_oscar_wins": 1},
])
def test_any_bar_alone_is_a_quality_only_request(kwargs):
    """Which quality field the parser reaches for on the same sentence is a coin
    flip, and it used to decide whether the user got twelve films or three."""
    from services.magic_search_ranking import is_quality_only_request
    from services.nlp_search import MovieSearchIntent

    assert is_quality_only_request(MovieSearchIntent(semantic_query="x", reasoning="r", **kwargs))


@pytest.mark.parametrize("kwargs", [
    {"min_metacritic": 75, "include_genres": ["Horror"]},
    {"min_vectorbox_score": 80, "year_min": 1970},
    {"min_imdb_rating": 8.0, "countries": ["Japan"]},
])
def test_a_bar_plus_a_subject_is_a_normal_search(kwargs):
    """The vector is meaningful once there is a subject, so it keeps the ranking."""
    from services.magic_search_ranking import is_quality_only_request
    from services.nlp_search import MovieSearchIntent

    assert not is_quality_only_request(MovieSearchIntent(semantic_query="x", reasoning="r", **kwargs))


def test_no_bar_is_not_a_quality_only_request():
    from services.magic_search_ranking import is_quality_only_request
    from services.nlp_search import MovieSearchIntent

    assert not is_quality_only_request(MovieSearchIntent(semantic_query="x", reasoning="r"))


@pytest.mark.parametrize("kwargs", [
    {"countries": ["South Korea"]},
    {"spoken_languages": ["Japanese"]},
    {"awards_contains": ["Palme"]},
    {"min_vectorbox_score": 80},
])
def test_postgres_side_filters_widen_the_fetch(kwargs):
    """These are applied AFTER the search, so they can only keep what the fetch
    returned. At 20 candidates 'thrillers coreanos' kept one film of 219 Korean
    ones in the catalogue; at 150 it returns Memories of Murder."""
    from services.magic_search_ranking import (
        SEARCH_FETCH_DEFAULT, SEARCH_FETCH_POST_FILTERED, search_fetch_limit,
    )
    from services.nlp_search import MovieSearchIntent

    assert SEARCH_FETCH_POST_FILTERED > SEARCH_FETCH_DEFAULT
    intent = MovieSearchIntent(semantic_query="x", reasoning="r", **kwargs)
    assert search_fetch_limit(intent) == SEARCH_FETCH_POST_FILTERED


def test_a_qdrant_only_query_does_not_pay_for_the_wide_fetch():
    """Genres, years and language ARE in the payload — Qdrant filters during the
    search, so twenty candidates are twenty real answers."""
    from services.magic_search_ranking import SEARCH_FETCH_DEFAULT, search_fetch_limit
    from services.nlp_search import MovieSearchIntent

    intent = MovieSearchIntent(semantic_query="x", reasoning="r",
                               include_genres=["Horror"], year_min=1970,
                               original_language="ja")
    assert search_fetch_limit(intent) == SEARCH_FETCH_DEFAULT


# --- audience requests: the failure no threshold can catch --------------------
#
# Measured 2026-07-29: "family friendly, gentle, wholesome, safe for all ages"
# scores 0.548 over its top ten neighbours — HIGHER than "the loneliness of
# living in a huge city" at 0.505, one of the queries the engine answers best.
# The vector is not weakly right, it is confidently wrong, so only whoever reads
# the sentence can tell an audience from a subject.


def test_audience_request_exists_and_defaults_off():
    from services.nlp_search import MovieSearchIntent

    assert "audience_request" in MovieSearchIntent.model_fields
    assert MovieSearchIntent.model_fields["audience_request"].default is False


@pytest.mark.parametrize("query", [
    "una peli familiar para ver con niños",
    "una peli que guste a padres e hijos, sin violencia ni sustos",
    "algo para ver con mis hijos",
    "una pelicula familiar",
    "a movie for the whole family",
    "something family friendly",
    "a film to watch with the kids",
])
def test_the_cue_list_catches_an_audience(query):
    from services.nlp_search import names_an_audience

    assert names_an_audience(query)


@pytest.mark.parametrize("query", [
    # An occasion, not an audience — which is why the cues carry their
    # preposition ("para ver con") instead of the bare verb.
    "una peli para ver un domingo por la tarde",
    "algo para ver esta noche",
    # The same word as a SUBJECT. "familiar"/"family" alone is ambiguous, so the
    # bare noun phrase is matched exactly and the adjective use is left alone.
    "un drama sobre una familia rota",
    "peliculas sobre secretos de familia",
    "a movie about a dysfunctional family",
    "algo lento y triste sobre el duelo",
])
def test_the_cue_list_leaves_subjects_and_occasions_alone(query):
    from services.nlp_search import names_an_audience

    assert not names_an_audience(query)


def test_the_guard_only_sets_never_clears():
    """The cue list is a floor on recall, not a definition — the model sees
    phrasings no list will cover, and must be allowed to say so."""
    from services.nlp_search import MovieSearchIntent, ensure_audience_request

    flagged = MovieSearchIntent(semantic_query="x", reasoning="r", audience_request=True)
    assert ensure_audience_request(flagged, "algo lento y triste sobre el duelo").audience_request

    missed = MovieSearchIntent(semantic_query="x", reasoning="r")
    assert ensure_audience_request(missed, "una peli familiar para ver con niños").audience_request
