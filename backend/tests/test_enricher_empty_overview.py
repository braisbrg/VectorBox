"""The enricher must refuse to enrich films with no real source text, so it
never hallucinates an embedding from the title/genre alone (the Wolf Totem /
tmdb 417613 phantom). Refusal = (legacy_fallback, None); model_id None means
the caller does not stamp has_enriched_embedding, keeping the film out of
gated recommendations."""
import asyncio

from services.cinematic_enricher import generate_cinematic_description


class _BoomClient:
    """Any attribute access asserts — proves the LLM is never called."""
    def __getattr__(self, _name):
        raise AssertionError("LLM was called for an overview-less film")


def _run(overview: str):
    return asyncio.run(
        generate_cinematic_description(
            title="Wolf Totem", overview=overview, genres=["Animation"],
            keywords=[], directors=[], cast=[], year=0, groq_client=_BoomClient(),
        )
    )


def test_empty_overview_refuses_enrichment():
    desc, model = _run("")
    assert model is None
    assert "Wolf Totem" in desc  # legacy fallback text


def test_whitespace_overview_refuses_enrichment():
    _, model = _run("   \n  ")
    assert model is None


def test_too_short_overview_refuses_enrichment():
    _, model = _run("A dog.")  # <20 chars → effectively empty
    assert model is None


if __name__ == "__main__":
    test_empty_overview_refuses_enrichment()
    test_whitespace_overview_refuses_enrichment()
    test_too_short_overview_refuses_enrichment()
    print("ok")
