"""El RRF, la sigmoide del tridente y el filtro de plataformas ES, probados sobre las de verdad.

La CI decía comprobarlos con `scripts/test_trident_math.py` y `scripts/test_es_whitelist.py`,
pero los dos scripts definían su PROPIA `reciprocal_rank_fusion`, su propia
`calculate_quality_weight` y su propio `filter_es_providers`, sin importar nada del backend:
habrían seguido en verde con las funciones reales rotas. Medido el 2026-11-03: ningún test de
`backend/tests/` llamaba a ninguna de las tres. Éstos sí, y los dos scripts se retiran.
"""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from routers.search import filter_es_providers
from services.clustering_service import ClusteringService
from services.recommendation_service import RecommendationService


def _movies(*ids):
    return [SimpleNamespace(id=i) for i in ids]


def _rrf(lists, **kwargs):
    svc = RecommendationService(db=Mock(), tmdb=Mock(), qdrant=Mock())
    return svc.reciprocal_rank_fusion(lists, **kwargs)


def test_rrf_suma_peso_entre_k_mas_rango_en_cada_lista():
    scores = _rrf([_movies(1, 2, 3), _movies(3, 1)], k=60)
    assert scores[1] == pytest.approx(1 / 60 + 1 / 61)
    assert scores[2] == pytest.approx(1 / 61)
    assert scores[3] == pytest.approx(1 / 62 + 1 / 60)
    # La que sale arriba en dos listas gana a la que sólo sale en una.
    assert scores[1] > scores[3] > scores[2]


def test_rrf_aplica_el_peso_de_cada_lista():
    scores = _rrf([_movies(1), _movies(2)], k=60, weights=[2.0, 0.5])
    assert scores[1] == pytest.approx(2.0 / 60)
    assert scores[2] == pytest.approx(0.5 / 60)


def test_la_sigmoide_del_tridente_conserva_su_rango_de_5_4x():
    """La curva se quedó propia (k=0.15, x0=65) porque domina el orden: 5,4x entre VBS 55 y 90.

    Unificarla con la de Magic Search (1,6x) se probó y se revirtió el 2026-08-11 (REV-5).
    Si alguien vuelve a tocarla, este test lo dice antes que el feed.
    """
    weight = ClusteringService(qdrant=Mock()).calculate_quality_weight
    assert weight(65) == 0.5
    assert weight(50) + weight(80) == pytest.approx(1.0)
    assert weight(90) / weight(55) == pytest.approx(5.4, rel=0.01)


def test_sin_vbs_el_peso_es_neutro():
    assert ClusteringService(qdrant=Mock()).calculate_quality_weight(None) == 0.5


def test_el_filtro_es_deja_solo_plataformas_de_espana_en_su_orden():
    assert filter_es_providers(["Hulu", "Netflix", "Peacock", "Movistar+", "Filmin"]) == [
        "Netflix",
        "Movistar+",
        "Filmin",
    ]
