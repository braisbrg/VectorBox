"""Hermetic tests for the enriched-vector gate (2026-07-13).

Seeded movies carry legacy-recipe vectors (`has_enriched_embedding=False`) that
live in an asymmetric text-space and pollute recommendation rankings. The gate:

  - `search_similar` filters `has_enriched_embedding=True` by DEFAULT; opt out
    with filters={"include_unenriched": True} (scripts/experiments only).
  - Every Qdrant payload writer stamps the flag; a point WITHOUT the key is
    excluded from all gated searches, so omission = silent disappearance.
  - `_qdrant_payload(m, enriched=...)` override guards the callers that build
    the payload BEFORE flipping the row flag (enrich_vectors/check_embeddings).

All hermetic: no DB, no network, no Qdrant server (mock stubs only).
"""
import os
import sys

import pytest

sys.path.append(os.getcwd())

from unittest.mock import AsyncMock, MagicMock

from qdrant_client.models import FieldCondition, MatchValue, PayloadSchemaType


def _mock_qdrant_service():
    from services.qdrant_service import QdrantService
    svc = QdrantService()
    svc.client = AsyncMock()
    svc.client.query_points = AsyncMock(return_value=MagicMock(points=[]))
    return svc


def _gate_conditions(query_filter):
    if query_filter is None:
        return []
    return [
        c for c in (query_filter.must or [])
        if isinstance(c, FieldCondition)
        and c.key == "has_enriched_embedding"
        and isinstance(c.match, MatchValue)
        and c.match.value is True
    ]


@pytest.mark.asyncio
async def test_search_similar_gates_unenriched_by_default():
    svc = _mock_qdrant_service()
    await svc.search_similar([0.0] * svc.VECTOR_SIZE)  # filters=None
    qf = svc.client.query_points.call_args.kwargs["query_filter"]
    assert len(_gate_conditions(qf)) == 1


@pytest.mark.asyncio
async def test_search_similar_opt_out():
    svc = _mock_qdrant_service()
    await svc.search_similar([0.0] * svc.VECTOR_SIZE, filters={"include_unenriched": True})
    qf = svc.client.query_points.call_args.kwargs["query_filter"]
    assert _gate_conditions(qf) == []
    assert qf is None  # no other filters -> no Filter at all


@pytest.mark.asyncio
async def test_gate_composes_with_existing_filters():
    svc = _mock_qdrant_service()
    await svc.search_similar(
        [0.0] * svc.VECTOR_SIZE,
        filters={"year_min": 2000, "min_vectorbox_score": 55},
    )
    qf = svc.client.query_points.call_args.kwargs["query_filter"]
    assert len(_gate_conditions(qf)) == 1
    keys = [c.key for c in qf.must if isinstance(c, FieldCondition)]
    assert "year" in keys and "vectorbox_score" in keys


def test_qdrant_payload_stamps_flag():
    from models.database import Movie
    from scripts.reembed_catalog import _qdrant_payload

    movie = Movie(tmdb_id=1, title="X")
    movie.has_enriched_embedding = False
    assert _qdrant_payload(movie)["has_enriched_embedding"] is False
    # override guards the enrich_vectors/check_embeddings ordering (payload is
    # built BEFORE the row flag flips to True)
    assert _qdrant_payload(movie, enriched=True)["has_enriched_embedding"] is True

    movie.has_enriched_embedding = True
    assert _qdrant_payload(movie)["has_enriched_embedding"] is True


def test_qdrant_payload_schema_has_flag():
    from models.external_schemas import QdrantPayload

    dumped = QdrantPayload(
        tmdb_id=1, title="X", has_enriched_embedding=True
    ).model_dump(exclude_none=True)
    assert dumped["has_enriched_embedding"] is True
    # populated-as-False must survive exclude_none (False is not None)
    dumped_false = QdrantPayload(
        tmdb_id=1, title="X", has_enriched_embedding=False
    ).model_dump(exclude_none=True)
    assert dumped_false["has_enriched_embedding"] is False


@pytest.mark.asyncio
async def test_init_payload_indexes_includes_bool():
    svc = _mock_qdrant_service()
    svc.client.create_payload_index = AsyncMock()
    await svc.init_payload_indexes()
    assert any(
        c.kwargs.get("field_name") == "has_enriched_embedding"
        and c.kwargs.get("field_schema") == PayloadSchemaType.BOOL
        for c in svc.client.create_payload_index.call_args_list
    )
