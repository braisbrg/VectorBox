"""The `original_language` guard — Fase -1.2.

Born from a measurement, not a hunch: on 2026-07-26 the same query, sent ten
times, came back with `original_language="es"` six times and `None` four. The
query ("algo lento y triste sobre el duelo, sin sustos") names no language, and
the model's own `reasoning` never mentioned one — so the field was improvisation,
and the two answers were materially different products (Pain and Glory / Cría
cuervos versus Drive My Car / Ordinary People).

These tests are offline: they exercise the deterministic guard, not the LLM.
A test that called Groq would be slow, flaky and would burn the daily quota that
started this whole investigation.
"""
import pytest

from services.nlp_search import (
    MovieSearchIntent,
    _query_names_a_language,
    guard_language_filter,
    normalize_language,
)


def _intent(**kw) -> MovieSearchIntent:
    kw.setdefault("semantic_query", "x")
    kw.setdefault("reasoning", "test fixture")
    return MovieSearchIntent(**kw)


# ── queries that must NOT keep a language filter ─────────────────────────────
@pytest.mark.parametrize("query", [
    "algo lento y triste sobre el duelo, sin sustos",   # the measured regression
    "something slow and sad about grief, no jump scares",
    "una peli de atracos con estilo",
    "a stylish heist movie",
    "películas para ver con mis padres",
    "terror psicológico de los 90",
])
def test_drops_language_when_query_never_asked(query):
    out = guard_language_filter(_intent(original_language="es"), query)
    assert out.original_language is None, f"guard let an unrequested filter through: {query!r}"


# ── queries that legitimately ask for one ────────────────────────────────────
@pytest.mark.parametrize("query,keep", [
    ("cine español de los 80", "es"),
    ("Korean cinema, slow burn", "ko"),
    ("algo en francés, ligero", "fr"),
    ("thrillers japoneses", "ja"),
    ("cine europeo de los 70", "fr"),          # region counts as a cue
    ("nordic noir", "sv"),
    ("películas en habla hispana", "es"),
    ("something foreign with subtitles", "ja"),
])
def test_keeps_language_when_query_asked(query, keep):
    out = guard_language_filter(_intent(original_language=keep), query)
    assert out.original_language == keep, f"guard dropped a legitimate filter: {query!r}"


def test_accents_do_not_matter():
    assert _query_names_a_language("cine japonés")
    assert _query_names_a_language("cine japones")


def test_guard_is_a_noop_when_the_model_set_nothing():
    out = guard_language_filter(_intent(original_language=None), "cine español")
    assert out.original_language is None


def test_guard_leaves_other_filters_alone():
    """The fix is surgical: the 'atracos europeos años 70' case must survive."""
    out = guard_language_filter(
        _intent(year_min=1970, year_max=1979, countries=["France", "Italy"], original_language="fr"),
        "atracos con mucho estilo, cine europeo de los 70",
    )
    assert (out.year_min, out.year_max) == (1970, 1979)
    assert out.countries == ["France", "Italy"]
    assert out.original_language == "fr"


def test_language_name_still_coerced_to_iso():
    """Pre-existing behaviour must not regress: the LLM sometimes says 'Spanish'."""
    assert normalize_language("Spanish") == "es"
    assert normalize_language("es") == "es"
    assert normalize_language("Klingon") is None
    assert _intent(original_language="Spanish").original_language == "es"


def test_client_disables_sdk_retries():
    """Fase -1.1: the SDK's own retry defeated PARSER_CHAIN's whole purpose.

    Guards the fix that took a 10-parse burst from a 16.4s median back to ~1.4s.
    """
    import inspect
    from services import nlp_search

    # Comment lines mention the flag too — count only real code.
    code = [ln for ln in inspect.getsource(nlp_search.get_llm_client).splitlines()
            if not ln.lstrip().startswith("#")]
    clients = sum("AsyncOpenAI(" in ln for ln in code)
    disabled = sum("max_retries=0" in ln for ln in code)
    assert clients == disabled == 2, (
        f"every LLM client must pass max_retries=0 (found {clients} clients, "
        f"{disabled} with the flag) — otherwise a 429 sleeps ~15s retrying the "
        "same model instead of falling through to PARSER_CHAIN[1]"
    )


# ── semantic_query nunca puede llegar vacío al embedder ──────────────────────
def test_empty_semantic_query_falls_back_to_the_raw_query():
    """Regression: an empty semantic_query 500'd the whole search.

    Found live 2026-07-28 with "algo que terminemos mis padres y yo sin
    discutir": the model returned a schema-valid intent with semantic_query="",
    which reached generate_embedding and raised "No text available for embedding
    generation". Reachable from ordinary user input, so it was a crash anyone
    could trigger by typing the wrong sentence.
    """
    from services.nlp_search import ensure_semantic_query, finalize_intent

    q = "algo que terminemos mis padres y yo sin discutir"
    for empty in ["", "   ", "\n"]:
        out = ensure_semantic_query(_intent(semantic_query=empty), q)
        assert out.semantic_query == q

    # a real expansion must survive untouched
    good = ensure_semantic_query(_intent(semantic_query="grief, mourning, loss"), q)
    assert good.semantic_query == "grief, mourning, loss"

    # and finalize_intent must apply BOTH repairs at once
    both = finalize_intent(_intent(semantic_query="", original_language="es"), q)
    assert both.semantic_query == q
    assert both.original_language is None


def test_both_llm_returns_go_through_finalize_intent():
    """Neither the primary nor the fallback may skip the repairs."""
    import inspect
    from services import nlp_search

    src = inspect.getsource(nlp_search.parse_user_intent)
    # Matched on `finalize_intent(await ` rather than on the callee's name: the
    # assertion used to spell out `finalize_intent(await client`, and adding an
    # OTel wrapper around the same call broke a test whose property was untouched.
    # What matters is that every model return is repaired, not who makes the call.
    assert src.count("finalize_intent(await ") == 2, (
        "both the primary and the fallback model call must be wrapped — "
        "an unwrapped path can still emit an empty semantic_query"
    )
    # And the give-up paths too, which is what keeps the deterministic guards
    # working when Groq is rate limited (measured: 8000 tokens per MINUTE).
    assert src.count("finalize_intent(MovieSearchIntent(") == 2, (
        "both give-up paths must finalize — otherwise a rate-limited user loses "
        "the audience/quality guards that need no model at all"
    )


# ── revisión 2026-07-28: países y falsos positivos por substring ─────────────
@pytest.mark.parametrize("query", [
    "cine de Corea del Sur",
    "películas de Japón",
    "algo de Francia, años 60",
    "films from France",
    "movies from Korea",
])
def test_country_names_count_as_cues(query):
    """The first cue list had adjectives but no country names — these all
    dropped a legitimate filter."""
    out = guard_language_filter(_intent(original_language="xx"), query)
    assert out.original_language == "xx", f"country name not recognised: {query!r}"


@pytest.mark.parametrize("query", [
    "movies like Chinatown",        # 'china' was a substring hit
    "algo como Indiana Jones",      # 'india' would be, with substring matching
])
def test_titles_containing_country_substrings_are_not_cues(query):
    out = guard_language_filter(_intent(original_language="zh"), query)
    assert out.original_language is None, f"substring false positive: {query!r}"


def test_stems_still_match_inflected_forms():
    assert _query_names_a_language("películas subtituladas")
    assert _query_names_a_language("cine extranjero, dobladas mejor")


@pytest.mark.parametrize("query", [
    "thrillers japoneses",     # -es plural, the regression the review itself caused
    "dramas coreanos",         # -s plural, ES
    "korean thrillers",        # -s plural, EN
    "películas francesas",
])
def test_plural_forms_still_match(query):
    assert _query_names_a_language(query), f"plural not recognised: {query!r}"
