"""Single source of truth for Groq LLM model IDs and their per-task chains.

Groq's free-tier lineup churns — models get decommissioned every few months
(Llama 4 Scout, Llama 3.1 8B, Llama 3.3 70B, and Qwen3-32B have all come and
gone). When a model dies, fix it HERE, not in enricher/parser/clustering
separately.

Verify the live lineup — never trust blogs, they go stale:
    GET https://api.groq.com/openai/v1/models   (Bearer $GROQ_API_KEY)

Last verified 2026-07-24: qwen3-32b GONE; live chat models = qwen/qwen3.6-27b,
openai/gpt-oss-120b, openai/gpt-oss-20b, llama-3.3-70b-versatile,
llama-3.1-8b-instant. The two quality leaders for our tasks are qwen3.6-27b
(prose) and gpt-oss-120b (prose/parsing) — no higher-tier model exists on the
free tier, so those two are the ceiling.
"""

# Reasoning models spend the token budget on chain-of-thought and leak it as
# inline <think>…</think> unless capped. Effort per model; absent = no override
# (non-reasoning model). qwen3 → 'none' (kill reasoning), gpt-oss → 'low' (it
# empties out entirely at default/high effort on long prompts).
REASONING_EFFORT = {
    "qwen/qwen3.6-27b": "none",
    "openai/gpt-oss-120b": "low",
    "openai/gpt-oss-20b": "low",
}

# Enrichment / cluster-naming — prose quality first. Each model has a SEPARATE
# TPM bucket, so a per-minute 429 on one cascades to the next instead of
# degrading to the legacy fallback. qwen3.6-27b = top-tier prose (own 8K bucket);
# gpt-oss-120b = richest (~81w); gpt-oss-20b = fast vendor-diverse floor.
ENRICH_CHAIN = [
    "qwen/qwen3.6-27b",
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
]

# Magic Box parsing — speed + determinism first (temperature=0, structured
# output). gpt-oss-120b parses in ~1.1s, 5/5 valid; qwen3.6-27b is the
# vendor-diverse fallback (can spike ~18s on some queries — fine as a rare
# fallback, wrong as primary).
PARSER_CHAIN = [
    "openai/gpt-oss-120b",
    "qwen/qwen3.6-27b",
]
