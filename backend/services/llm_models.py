"""Single source of truth for Groq LLM model IDs and their per-task chains.

Groq's free-tier lineup churns — models get decommissioned every few months
(Llama 4 Scout, Llama 3.1 8B, Llama 3.3 70B, and Qwen3-32B have all come and
gone). When a model dies, fix it HERE, not in enricher/parser/clustering
separately.

Verify the live lineup — never trust blogs, they go stale:
    GET https://api.groq.com/openai/v1/models   (Bearer $GROQ_API_KEY)

Last verified 2026-09-01 contra /v1/models: los chat models vivos son
qwen/qwen3.8-27b, qwen/qwen3.6-27b, openai/gpt-oss-120b, openai/gpt-oss-20b,
openai/gpt-oss-safeguard-20b y groq/compound(-mini). **Las dos llama que había
aquí (llama-3.3-70b-versatile, llama-3.1-8b-instant) ya NO existen** — es lo que
pasa con una lista escrita a mano: envejece sin avisar y nadie la ejecuta.

qwen3.6-27b quedó deprecado el 2026-09-01 y se apaga el **14 de septiembre**;
Groq enruta solo a partir de esa fecha, pero un ID muerto en el código es un
fallo silencioso esperando, así que se cambió antes. Sustituto: qwen/qwen3.8-27b.
Los dos líderes de calidad para nuestras tareas siguen siendo el qwen (prosa) y
gpt-oss-120b (prosa/parsing) — no hay nada superior en el tramo gratuito.
"""

# Reasoning models spend the token budget on chain-of-thought and leak it as
# inline <think>…</think> unless capped. Effort per model; absent = no override
# (non-reasoning model). qwen3 → 'none' (kill reasoning), gpt-oss → 'low' (it
# empties out entirely at default/high effort on long prompts).
REASONING_EFFORT = {
    "qwen/qwen3.8-27b": "none",
    "openai/gpt-oss-120b": "low",
    "openai/gpt-oss-20b": "low",
}

# Enrichment / cluster-naming — prose quality first. Each model has a SEPARATE
# TPM bucket, so a per-minute 429 on one cascades to the next instead of
# degrading to the legacy fallback. qwen3.8-27b = top-tier prose (own 8K bucket);
# gpt-oss-120b = richest (~81w); gpt-oss-20b = fast vendor-diverse floor.
ENRICH_CHAIN = [
    "qwen/qwen3.8-27b",
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
]

# Magic Box parsing — speed + determinism first (temperature=0, structured
# output). gpt-oss-120b parses in ~1.1s, 5/5 valid; el qwen es el fallback de
# proveedor distinto.
# ⚠ El "puede dispararse a ~18s en algunas consultas — vale de fallback raro, no
# de primario" se midió sobre qwen3.6-27b y NO se ha re-medido en 3.8: hereda el
# puesto, no el número. Un smoke live del 2026-09-01 dio 0,44s en prosa y 0,17s
# en JSON, pero una llamada no es una distribución de latencia. Antes de
# ascenderlo a primario hay que volver a medir la cola, no el caso medio.
PARSER_CHAIN = [
    "openai/gpt-oss-120b",
    "qwen/qwen3.8-27b",
]
