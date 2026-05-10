"""Tests for the typo normalisation layer in nlp_search.

These guard against accidental over-matching: the dict is curated to fix
LLM regressions on Spanish slang (quinqui) and common informal spellings,
but must NOT alter user queries containing legitimate film titles or
unrelated words.
"""
from services.nlp_search import _normalize_typos


def test_quinki_to_quinqui():
    assert _normalize_typos("recomiéndame algo de cine quinki") == \
        "recomiéndame algo de cine quinqui"


def test_kinki_to_quinqui():
    assert _normalize_typos("películas kinki de los 80") == \
        "películas quinqui de los 80"


def test_case_insensitive():
    assert _normalize_typos("Cine QUINKI español") == "Cine quinqui español"


def test_word_boundary_no_substring_match():
    # "quinkin" or "kinkier" must not be touched — only whole-word matches.
    assert _normalize_typos("quinkin") == "quinkin"
    assert _normalize_typos("kinkier") == "kinkier"
    assert _normalize_typos("scifiteen") == "scifiteen"  # not in dict but illustrates principle


def test_romcom_variants():
    assert _normalize_typos("a good romcom") == "a good romantic comedy"
    assert _normalize_typos("rom com from the 90s") == "romantic comedy from the 90s"


def test_jhorror_variants():
    assert _normalize_typos("classic jhorror") == "classic j-horror"
    assert _normalize_typos("j horror like Ringu") == "j-horror like Ringu"


def test_no_change_for_unrelated_query():
    queries = [
        "movies like Inception",
        "Spanish drama from the 70s",
        "best film noir of all time",  # 'noir' is canonical, not touched
        "dame una película parecida a deprisa deprisa",
    ]
    for q in queries:
        assert _normalize_typos(q) == q, f"Should not change: {q!r}"


def test_empty_string_passes_through():
    assert _normalize_typos("") == ""
    assert _normalize_typos(None) is None  # type: ignore


def test_does_not_break_punctuation_or_accents():
    # Punctuation around the typo word must survive.
    assert _normalize_typos("¿quinki?") == "¿quinqui?"
    assert _normalize_typos("'quinki'") == "'quinqui'"
    assert _normalize_typos("quinki, deprisa") == "quinqui, deprisa"


def test_multiple_replacements_in_one_query():
    out = _normalize_typos("a quinki romcom about kinki kids")
    assert "quinqui" in out
    assert "romantic comedy" in out
    # Both quinki and kinki should be rewritten:
    assert "quinki" not in out.lower()
    assert "kinki" not in out.lower()
