"""The 429 handling in cinematic_enricher branches on two string parses.

If either silently stops matching — Groq rewords the message, someone tightens the
regex — the enricher stops waiting and cascades every film through the whole chain
to the legacy fallback, with no error anywhere. That is exactly the failure this
guards: it looks like "the models are down", it is a parse miss.

Messages below are the real Groq wording (verified 2026-08-06).
"""
import pytest

from services.cinematic_enricher import _is_daily_limit, _parse_retry_after

TPM = (
    "Error code: 429 - Rate limit reached for model `qwen/qwen3.6-27b` in organization "
    "`org_x` service tier `on_demand` on tokens per minute (TPM): Limit 8000, Used 7800, "
    "Requested 510. Please try again in 2.25s."
)
TPM_LONG = "Error code: 429 - ... on tokens per minute (TPM): Limit 8000. Please try again in 1m26.4s."
TPD = "Error code: 429 - ... on tokens per day (TPD): Limit 200000. Please try again in 4m2.9s."
RPD = "Error code: 429 - ... on requests per day (RPD): Limit 1000. Please try again in 1h2m3s."


@pytest.mark.parametrize("msg,expected", [(TPM, 2.25), (TPM_LONG, 86.4), (TPD, 242.9)])
def test_retry_after_is_parsed(msg, expected):
    assert _parse_retry_after(msg) == pytest.approx(expected)


def test_unparsable_wait_returns_none_rather_than_zero():
    # None means "could not tell" → try the next model. A 0.0 would busy-loop.
    assert _parse_retry_after(RPD) is None
    assert _parse_retry_after("Error code: 500 - upstream boom") is None


@pytest.mark.parametrize("msg,daily", [(TPM, False), (TPM_LONG, False), (TPD, True), (RPD, True)])
def test_daily_vs_per_minute(msg, daily):
    # Per-minute → wait and retry the SAME model. Per-day → that model is done for
    # the day, move on. Getting this backwards either burns the chain in seconds or
    # sleeps on a limit that will not reset for hours.
    assert _is_daily_limit(msg) is daily
