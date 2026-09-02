"""TMDB serves en-US by default; non-English films often have no English synopsis.

Without a fallback the film reaches the enricher with an empty overview, gets refused
by the anti-hallucination guard, and silently stays out of gated recommendations —
47 of the catalogue's 76 unenrichable films were in exactly that state (2026-08-06),
each with a usable synopsis in the translations already being downloaded.
"""
from services.tmdb_client import pick_fallback_overview

GL = "Xosé Manuel Beiras repasa a súa traxectoria seguindo dúas pistas."
ES = "Urbano, que acaba de salir de la cárcel, recoge en su taxi a unas prostitutas."
FR = "Après avoir appris qu'il ne lui reste que peu de temps à vivre, un homme."


def test_prefers_the_films_own_language():
    code, text = pick_fallback_overview("gl", {"gl": GL, "es": ES, "fr": FR})
    assert (code, text) == ("gl", GL)


def test_falls_back_to_spanish_when_original_is_missing():
    # Japanese film, no Japanese synopsis on file → Spanish, the other UI locale.
    code, text = pick_fallback_overview("ja", {"es": ES, "fr": FR})
    assert (code, text) == ("es", ES)


def test_takes_anything_over_nothing():
    code, text = pick_fallback_overview("ja", {"fr": FR})
    assert (code, text) == ("fr", FR)


def test_no_translations_returns_none_rather_than_empty_string():
    # None must reach the caller so it leaves `overview` untouched: an empty string
    # would overwrite the field and look like a successful fallback.
    assert pick_fallback_overview("en", {}) == (None, None)


def test_unknown_original_language_does_not_crash():
    assert pick_fallback_overview(None, {"fr": FR}) == ("fr", FR)
