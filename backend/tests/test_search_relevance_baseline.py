"""Ancla la calidad de búsqueda medida el 2026-08-03 — Fase 0.

Esto no busca bugs: fija un SUELO. Cada fase del plan de búsqueda (sparse BM25,
fusión RRF en el Query API, reranking) tiene que superar estos números o no
entra, y si algo los baja sin querer, salta aquí en vez de descubrirse tres
sesiones después mirando una fila rara.

Baseline con el canal léxico APAGADO, que es como se sirve hoy:

    nDCG@10 medio    0.826
    Recall@20 medio  0.689
    MRR@5 entidad    0.900

Los umbrales van por debajo de lo medido: el catálogo crece y se re-enriquece, y
un test que exige el número exacto se convierte en ruido que todo el mundo
aprende a ignorar. Lo que vigila es una CAÍDA, no una oscilación.

    docker compose exec backend python -m pytest tests/test_search_relevance_baseline.py -m integration -q
"""
import pytest
import pytest_asyncio

from config import engine
from scripts.eval_search import eval_descriptive, eval_entity
from services import lexical_channel
from services.embedding_service import EmbeddingService
from services.qdrant_service import QdrantService
from services.tmdb_client import TMDBClient

pytestmark = pytest.mark.integration

# Medido 2026-08-03, menos el margen de deriva del catálogo.
MIN_NDCG = 0.78
MIN_RECALL = 0.64
MIN_MRR = 0.85

# Las dos consultas que el catálogo peor responde. Se listan para que su suelo
# sea explícito: si alguien "mejora" la media hundiendo justo estas, se ve.
FLOOR_PER_QUERY = 0.30


@pytest_asyncio.fixture(autouse=True)
async def _fresh_pool():
    default = lexical_channel.ENABLED
    await engine.dispose()
    yield
    lexical_channel.ENABLED = default
    await engine.dispose()


@pytest.mark.asyncio
async def test_descriptive_search_holds_its_baseline():
    rows = await eval_descriptive(TMDBClient(), QdrantService(), EmbeddingService(), False)
    ndcg = sum(r["ndcg@10"] for r in rows) / len(rows)
    recall = sum(r["recall@20"] for r in rows) / len(rows)
    peor = min(rows, key=lambda r: r["ndcg@10"])
    assert ndcg >= MIN_NDCG, f"nDCG@10 medio {ndcg:.3f} < {MIN_NDCG} — peor: {peor['query']}"
    assert recall >= MIN_RECALL, f"Recall@20 medio {recall:.3f} < {MIN_RECALL}"
    assert peor["ndcg@10"] >= FLOOR_PER_QUERY, (
        f"{peor['query']} cayó a {peor['ndcg@10']:.3f}: una media sana puede tapar "
        f"una consulta rota"
    )


@pytest.mark.asyncio
async def test_entity_lookup_holds_its_baseline():
    """Teclear el nombre de una película tiene que dar ESA película. Cubre los
    tres caminos que estaban rotos: title_es, acentos y puntuación."""
    rows = await eval_entity()
    mrr = sum(r["rr"] for r in rows) / len(rows)
    fallos = [r["query"] for r in rows if not r["rr"]]
    assert mrr >= MIN_MRR, f"MRR@5 {mrr:.3f} < {MIN_MRR} — fallan: {fallos}"


@pytest.mark.asyncio
async def test_the_metric_is_reproducible():
    """La Fase 0 sólo vale si dos pasadas dan lo mismo. Sin esto, cualquier
    comparación entre fases mediría el ruido."""
    a = await eval_descriptive(TMDBClient(), QdrantService(), EmbeddingService(), False)
    b = await eval_descriptive(TMDBClient(), QdrantService(), EmbeddingService(), False)
    assert [r["ndcg@10"] for r in a] == [r["ndcg@10"] for r in b]
