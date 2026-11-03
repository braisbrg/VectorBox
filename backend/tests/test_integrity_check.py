"""El chequeo de integridad ve lo que la auditoría del 2026-11-03 encontró, y sólo eso.

CUADERNO E115–E118: 1.451 vectores de la receta cruda bajo la marca de enriquecidos, el payload de
Qdrant desfasado de Postgres en siete filtros (26 películas cambiaban de decisión en el de país),
1.584 puntos con la clave vieja `rating`. Nada falló ni avisó. `scripts/check_integrity.py` es la
guarda que faltaba; esto comprueba que caza esas tres formas y que no grita por la trampa que ya
engañó a la primera auditoría: 6.264 «claves ausentes» que eran campos NULL en la fila (playbook B1).
"""
from unittest.mock import MagicMock

import numpy as np

from models.database import Movie
from scripts.check_integrity import payload_problems, vector_drift
from services import embedding_service
from services.embedding_service import EmbeddingService, embedding_text


def _movie(**kw):
    m = Movie(tmdb_id=848685, title="The Beasts", year=2022, genres=["Drama", "Thriller"],
              overview="A French couple in rural Galicia.", vote_average=7.6, vote_count=1200,
              runtime=137, original_language="es", keywords=["galicia"], omdb_countries=["Spain"],
              omdb_languages=["Spanish"], mpaa_rating=None, oscar_wins=0, is_adult=False,
              has_enriched_embedding=True)
    for k, v in kw.items():
        setattr(m, k, v)
    return m


def test_payload_canonico_no_da_nada():
    from models.external_schemas import qdrant_payload
    m = _movie()
    assert payload_problems([m], {m.tmdb_id: qdrant_payload(m)}) == []


def test_caza_lo_que_cambia_un_filtro_y_la_clave_vieja():
    from models.external_schemas import qdrant_payload
    m = _movie()
    stale = qdrant_payload(m)
    del stale["countries"]                       # fila con país, punto sin él: invisible al filtro
    stale["vote_count"] = 199                    # la fila dice 1200
    stale["rating"] = stale.pop("vote_average")  # el nombre viejo
    got = sorted(payload_problems([m], {m.tmdb_id: stale}))
    assert got == sorted([(848685, "missing", "countries"), (848685, "differs", "vote_count"),
                          (848685, "missing", "vote_average"), (848685, "extra", "rating")])


def test_no_grita_por_lo_que_filtra_igual():
    from models.external_schemas import qdrant_payload
    m = _movie()
    p = qdrant_payload(m)
    del p["mpaa_rating"]                          # NULL en la fila: ausente y null filtran igual
    p["genres"] = list(reversed(p["genres"]))     # mismo conjunto, otro orden
    assert payload_problems([m], {m.tmdb_id: p}) == []


def test_vector_de_otro_texto_se_detecta():
    rng = np.random.default_rng(0)
    good, other = rng.normal(size=768), rng.normal(size=768)
    stored = {1: good, 2: other}
    encoded = {1: good * 3.0, 2: good}            # misma dirección = mismo texto
    assert [t for t, _ in vector_drift(stored, encoded)] == [2]


def test_embedding_text_es_lo_que_codifica_produccion(monkeypatch):
    model = MagicMock()
    model.encode = MagicMock(return_value=np.zeros(768))
    monkeypatch.setattr(embedding_service, "get_model", lambda: model)
    film = {"title": "The Beasts", "overview": "A French couple in rural Galicia.",
            "genres": ["Drama", "Thriller"], "keywords": [f"k{i}" for i in range(20)]}

    EmbeddingService().generate_embedding(film)
    raw = model.encode.call_args[0][0]
    assert raw == embedding_text(film)
    assert raw == ("A French couple in rural Galicia.. Genres: Drama, Thriller. Themes: "
                   + ", ".join(f"k{i}" for i in range(15)))

    EmbeddingService().generate_embedding(film, text_override="An LLM description.")
    assert model.encode.call_args[0][0] == "An LLM description." == embedding_text(
        {**film, "text_override": "An LLM description."})
