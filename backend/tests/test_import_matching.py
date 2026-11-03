"""El import empareja cada película de Letterboxd con la que ES, no con la primera que da TMDB.

Medido el 2026-11-03 (CUADERNO E115): 18 filas del catálogo llevaban la URI de
Letterboxd de OTRA película, y 7 valoraciones de 2 usuarios apuntaban a la
equivocada. «X-Men» (2000) era *X-Men: The Mutant Watch*, «X2» un webcast, «Here
We Are» *We Are Here*. Dos causas, las dos aquí:

- `search_movie` devolvía `results[0]`, el más popular, se llamara como se llamara.
- El import tiraba las URIs `letterboxd.com/tmdb/N` («no slug») aunque el N ya
  decía qué película era, y buscaba por título.

Y el script que debía cazarlo buscaba cada película por su propio título: una fila
equivocada se encontraba a sí misma, y su `--fix` movía valoraciones al homónimo
más popular.

Los resultados de TMDB de estos tests copian la forma y el orden reales: en todos
los casos la película buena estaba en la lista, detrás.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from routers.upload import resolve_tmdb_id
from scripts.reconcile_letterboxd_movies import audit
from services.csv_parser import CSVParser
from services.scraper_service import ScraperService, _tmdb_id_from_film_html
from services.tmdb_client import TMDBClient, pick_search_result


def r(tmdb_id, title, year, original=None):
    return {"id": tmdb_id, "title": title, "original_title": original or title,
            "release_date": f"{year}-06-01" if year else ""}


MUTANT_WATCH = r(447399, "X-Men: The Mutant Watch", 2000)
X_MEN = r(36657, "X-Men", 2000)


def test_gana_el_titulo_no_el_primer_resultado():
    assert pick_search_result([MUTANT_WATCH, X_MEN], "X-Men", 2000)["id"] == 36657
    assert pick_search_result([r(880728, "The Test", 2021), r(749645, "The Beta Test", 2021)],
                              "The Beta Test", 2021)["id"] == 749645


def test_estricto_no_inventa_si_ningun_titulo_coincide():
    we_are_here = [r(917339, "We Are Here", 2021)]
    assert pick_search_result(we_are_here, "Here We Are", 2021, strict=True) is None
    # Fuera del import (Magic Box, seed) se conserva el primero como último recurso.
    assert pick_search_result(we_are_here, "Here We Are", 2021)["id"] == 917339


def test_entre_homonimos_decide_el_ano():
    zootopia = [r(269149, "Zootopia", 2016), r(1084242, "Zootopia 2", 2025)]
    assert pick_search_result(zootopia, "Zootopia 2", 2025, strict=True)["id"] == 1084242
    scarlet = [r(840111, "Scarlet", 2023), r(1017101, "Scarlet", 2022)]
    assert pick_search_result(scarlet, "Scarlet", 2022, strict=True)["id"] == 1017101
    # El único que se llama así está a 9 años: estricto, nada.
    assert pick_search_result([r(269149, "Zootopia", 2016)], "Zootopia", 2025, strict=True) is None


def test_titulo_original_acentos_y_ampersand_cuentan_como_el_mismo():
    assert pick_search_result([r(1, "Something Else", 2001), r(194, "Amélie", 2001)],
                              "Amelie", 2001, strict=True)["id"] == 194
    assert pick_search_result([r(2, "La Haine", 1995, original="La Haine")], "la haine", 1995,
                              strict=True)["id"] == 2
    assert pick_search_result([r(3, "Tom and Jerry", 2021)], "Tom & Jerry", 2021,
                              strict=True)["id"] == 3


def test_la_uri_tmdb_dice_que_pelicula_es():
    assert CSVParser.tmdb_id_from_letterboxd_uri("https://letterboxd.com/tmdb/36657") == 36657
    assert CSVParser.tmdb_id_from_letterboxd_uri(" letterboxd.com/tmdb/36657/ ") == 36657
    assert CSVParser.tmdb_id_from_letterboxd_uri("https://letterboxd.com/film/x-men/") is None
    assert CSVParser.tmdb_id_from_letterboxd_uri("https://boxd.it/KU2o") is None
    assert CSVParser.tmdb_id_from_letterboxd_uri(None) is None


def test_search_movie_estricto_de_extremo_a_extremo(caplog):
    """El método entero, con la red y Redis como únicos stubs."""
    client = TMDBClient()
    store = {}
    redis = SimpleNamespace(get=AsyncMock(side_effect=lambda k: store.get(k)),
                            setex=AsyncMock(side_effect=lambda k, ttl, v: store.__setitem__(k, v)))
    client._get_redis = AsyncMock(return_value=redis)
    client._make_request = AsyncMock(return_value={"results": [MUTANT_WATCH, X_MEN]})

    assert asyncio.run(client.search_movie("X-Men", 2000, strict=True))["id"] == 36657
    # La segunda sale de la caché, que guarda la lista y no la elección.
    assert asyncio.run(client.search_movie("X-Men", 2000, strict=True))["id"] == 36657
    assert client._make_request.await_count == 1
    assert asyncio.run(client.search_movie("Here We Are", 2021, strict=True)) is None


def test_reconcile_encuentra_la_fila_que_lleva_la_uri_de_otra():
    def m(tmdb_id, title, uri, original=None):
        return SimpleNamespace(id=tmdb_id, tmdb_id=tmdb_id, title=title, original_title=original,
                               letterboxd_uri=uri, year=None)
    rows = [
        m(447399, "X-Men: The Mutant Watch", "https://letterboxd.com/tmdb/36657"),
        m(36657, "X-Men", "https://letterboxd.com/tmdb/36657"),
        m(496243, "Parasite", "https://letterboxd.com/film/parasite-2019/"),
        m(1502523, "THE ORAL CIGARETTES: PARASITE DEJAVU", "https://letterboxd.com/film/parasite-2019/"),
        m(129, "Spirited Away", "https://letterboxd.com/film/spirited-away/"),
    ]
    sure, shared, slugs = audit(rows)
    assert [(x.tmdb_id, n) for x, n in sure] == [(447399, 36657)]
    assert sorted(len(g) for g in shared) == [2, 2]
    assert [x.tmdb_id for _, x, _ in slugs] == [1502523]


# --- Lo que sólo Letterboxd sabe (2026-11-03, CUADERNO E118) ---------------------------
# Las 889 películas de biblioteca con enlace boxd.it, resueltas contra Letterboxd: 20 estaban mal.
# Homónimos que el título no separa (dos «Lullaby» de 2022: la buena era *Cinco lobitos*) y series
# emparejadas con lo que se llamara parecido («Chernobyl» → *Chernobyl: Zone of Exclusion*).

LULLABY_US = r(702621, "Lullaby", 2022)
LULLABY_ES = r(835504, "Lullaby", 2022, original="Cinco lobitos")


def _tmdb_with(results_by_title):
    client = TMDBClient()
    store = {}
    client._get_redis = AsyncMock(return_value=SimpleNamespace(
        get=AsyncMock(side_effect=lambda k: store.get(k)),
        setex=AsyncMock(side_effect=lambda k, ttl, v: store.__setitem__(k, v))))
    client._make_request = AsyncMock(side_effect=lambda path, params: {
        "results": results_by_title.get(params["query"], [])})
    return client


def test_homonimos_empatados_no_se_eligen_en_estricto():
    assert pick_search_result([LULLABY_US, LULLABY_ES], "Lullaby", 2022, strict=True) is None
    assert pick_search_result([LULLABY_US, LULLABY_ES], "Lullaby", 2022)["id"] == 702621


def test_lo_dudoso_lo_decide_letterboxd_y_lo_claro_no_le_pregunta():
    tmdb = _tmdb_with({"Lullaby": [LULLABY_US, LULLABY_ES], "X-Men": [MUTANT_WATCH, X_MEN],
                       "Chernobyl": [r(793445, "Chernobyl: Zone of Exclusion", 2019)]})
    lb = AsyncMock(side_effect=lambda uri: {"https://boxd.it/LULL": 835504}.get(uri))

    assert asyncio.run(resolve_tmdb_id("Lullaby", 2022, "https://boxd.it/LULL", tmdb, lb)) == 835504
    # Una serie: Letterboxd no da película, y no se empareja con nada.
    assert asyncio.run(resolve_tmdb_id("Chernobyl", 2019, "https://boxd.it/mkbG", tmdb, lb)) is None
    assert lb.await_count == 2

    # Claro por la URI o por el título: ni una petición a Letterboxd.
    assert asyncio.run(resolve_tmdb_id("Whatever", 2000, "https://letterboxd.com/tmdb/36657", tmdb, lb)) == 36657
    assert asyncio.run(resolve_tmdb_id("X-Men", 2000, "https://boxd.it/XMEN", tmdb, lb)) == 36657
    assert lb.await_count == 2


def test_la_pagina_de_una_serie_no_trae_pelicula():
    assert _tmdb_id_from_film_html('<html><body data-tmdb-id=""></body></html>') is None
    assert _tmdb_id_from_film_html('<html><body data-tmdb-id="848685"></body></html>') == 848685
    assert _tmdb_id_from_film_html(
        '<html><body><a href="https://www.themoviedb.org/movie/835504/">TMDB</a></body></html>') == 835504


def test_resolve_film_url_cachea_tambien_el_no(monkeypatch):
    """Un enlace corto se pide una vez; la respuesta, sea película o serie, queda en la caché."""
    import services.scraper_service as ss
    monkeypatch.setattr(ss, "_jittered_sleep", AsyncMock())
    store = {}
    s = ScraperService()
    s._get_redis = AsyncMock(return_value=SimpleNamespace(
        get=AsyncMock(side_effect=lambda k: store.get(k)),
        set=AsyncMock(side_effect=lambda k, v, ex=None: store.__setitem__(k, v))))
    pages = {"https://boxd.it/mkbG": '<body data-tmdb-id=""></body>',
             "https://boxd.it/LULL": '<body data-tmdb-id="835504"></body>'}
    s._fetch_with_curl_cffi = AsyncMock(side_effect=lambda url: pages[url])

    for _ in range(2):
        assert asyncio.run(s.resolve_film_url("https://boxd.it/mkbG")) is None
        assert asyncio.run(s.resolve_film_url("https://boxd.it/LULL")) == 835504
    assert s._fetch_with_curl_cffi.await_count == 2
