"""Una película recién dada de alta entra en Qdrant con el payload canónico, no con uno a mano.

Medido el 2026-11-03 (CUADERNO E115, E118): `MovieFactory.build_movie` y el lote de vectores del
import del ZIP construían el `PointStruct` con un dict escrito a mano. Llevaba la clave vieja
`rating` en vez de `vote_average`, y le faltaban países, idiomas, clasificación, Óscar, marca de
adulta, idioma original, sinopsis y ejes de mood. Cada alta nueva quedaba invisible a esos filtros
hasta que alguien sincronizara el payload: 1.584 puntos llevaban `rating` y 26 películas cambiaban
de decisión en el filtro de país.

`test_qdrant_payload_writers` no lo veía porque sólo mira las llamadas a `upsert_movie_vector`, y
estos dos escritores no pasan por ahí. Este test construye la película de verdad, con stubs sólo
en la frontera (TMDB, OMDb, el modelo de embedding), y compara el punto con el constructor
canónico.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import numpy as np

from models.external_schemas import OMDbResponse, qdrant_payload
from services.movie_factory import MovieFactory
from services.omdb_client import OMDbClient

DETAILS = {
    "id": 848685, "title": "The Beasts", "original_title": "As bestas", "release_date": "2022-11-11",
    "runtime": 137, "genres": [{"name": "Drama"}, {"name": "Thriller"}],
    "overview": "Antoine and Olga, a French couple, have been living in a small village in Galicia for a long time.",
    "poster_path": "/p.jpg", "backdrop_path": "/b.jpg", "vote_average": 7.6, "vote_count": 1200,
    "popularity": 20.0, "original_language": "es", "imdb_id": "tt15006566", "adult": False,
    "directors": ["Rodrigo Sorogoyen"], "cast": ["Denis Ménochet", "Marina Foïs"],
    "keywords_flat": ["galicia", "rural"], "title_es": "As bestas", "overview_es": "Antoine y Olga…",
}


def _factory():
    tmdb = MagicMock()
    tmdb.get_movie_details = AsyncMock(return_value=DETAILS)
    omdb = OMDbClient.__new__(OMDbClient)  # sin __init__: la fórmula del VBS es pura
    omdb.fetch_movie_data = AsyncMock(return_value=OMDbResponse(
        Response="True", imdbRating="7.5", imdbVotes="45,000", Metascore="80", Rated="Not Rated",
        Awards="Won 9 BAFTA Film Awards", Country="Spain, France", Language="Spanish, French, Galician"))
    emb = MagicMock()
    emb.generate_embedding = MagicMock(return_value=np.ones(768, dtype=np.float32))
    return MovieFactory(tmdb, omdb, emb, groq_client=None)


def test_el_punto_de_un_alta_lleva_el_payload_canonico():
    movie, point, _ = asyncio.run(_factory().build_movie(848685))
    assert point.payload == qdrant_payload(movie)


def test_un_alta_es_visible_a_los_filtros_que_antes_perdia():
    movie, point, _ = asyncio.run(_factory().build_movie(848685))
    p = point.payload
    assert "rating" not in p and p["vote_average"] == 7.6
    assert p["countries"] == ["Spain", "France"]
    assert p["spoken_languages"] == ["Spanish", "French", "Galician"]
    assert p["original_language"] == "es" and p["is_adult"] is False and p["mpaa_rating"] == "Not Rated"
    assert "mood_gravedad" in p and "mood_humanidad" in p
