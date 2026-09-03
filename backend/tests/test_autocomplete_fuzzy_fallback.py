"""El fallback difuso del autocompletado, y el comodín que colaba por el ILIKE.

Dos cosas, las dos medidas antes de escribirse (2026-08-10):

1. TMDB no tiene búsqueda difusa y devuelve CERO ante una errata de una letra
   ("intersteller", "shawshenk redemption"). El fallback de trigramas sobre nuestro
   catálogo las recupera, pero tiene que CALLARSE cuando no hay nada parecido: una
   sugerencia inventada manda al usuario a otra película, que es peor que un
   desplegable vacío. El umbral 0.45 sale de que el acierto más flojo puntúa 0.500
   y el mejor falso positivo 0.370.

2. El término se interpolaba en el ILIKE de directores, así que un `%` del usuario
   entraba como comodín: `%%%` casaba con TODOS los directores y el desplegable
   contestaba con las 8 películas de mejor VBS. Sin agujero de inyección (la
   consulta va parametrizada), pero contestando a quien no ha preguntado nada.

Hermético: ni TMDB ni Postgres. El banco con servicios en vivo es
`scripts/eval_searchbar.py`.
"""
import asyncio

import pytest
from sqlalchemy import func

from models.database import Movie
from routers.search import FUZZY_MIN_SIMILARITY, _fuzzy_title_rows


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self._rows


class _FakeDB:
    """Devuelve filas ya ordenadas por `sim`, como hace la consulta real."""

    def __init__(self, rows):
        self.rows = rows

    async def execute(self, *_a, **_k):
        return _FakeResult(self.rows)


def _row(title, sim, tmdb_id=1, year=2000):
    return {
        "tmdb_id": tmdb_id, "title": title, "year": year,
        "poster_path": "/p.jpg", "overview": "", "sim": sim,
    }


def test_keeps_matches_above_the_threshold():
    rows = [_row("Interstellar", 0.625, 157336, 2014), _row("Interstate 60", 0.350, 2, 2002)]
    out = asyncio.run(_fuzzy_title_rows(_FakeDB(rows), "intersteller"))
    assert [m["title"] for m in out] == ["Interstellar"], "el 0.350 no debe pasar"
    assert out[0]["id"] == 157336
    assert out[0]["release_date"] == "2014", "el año se sirve con la forma de TMDB"


def test_says_nothing_when_nothing_is_close():
    # Consulta sin sentido: el mejor parecido del catálogo fue 0.370 al medirlo.
    rows = [_row("Evil Does Not Exist", 0.370), _row("Exists", 0.333)]
    assert asyncio.run(_fuzzy_title_rows(_FakeDB(rows), "zzzqqq no existe nada")) == []


def test_stops_at_the_first_row_below_the_threshold():
    """Vienen ordenadas: la primera por debajo del umbral cierra la lista.

    Si en vez de cortar se filtrase fila a fila, una fila mal ordenada por el
    desempate de VBS podría colar algo flojo por detrás de algo bueno.
    """
    rows = [_row("Bueno", 0.90), _row("Flojo", 0.10), _row("Bueno tarde", 0.95)]
    out = asyncio.run(_fuzzy_title_rows(_FakeDB(rows), "bueno"))
    assert [m["title"] for m in out] == ["Bueno"]


def test_threshold_sits_between_the_two_measured_populations():
    # 0.500 fue el acierto más flojo ("parasyte"), 0.370 el mejor falso positivo.
    assert 0.370 < FUZZY_MIN_SIMILARITY < 0.500


def test_year_none_does_not_fabricate_a_release_date():
    rows = [_row("Sin año", 0.80, 9, None)]
    out = asyncio.run(_fuzzy_title_rows(_FakeDB(rows), "sin ano"))
    assert out[0]["release_date"] is None


@pytest.mark.parametrize("term", ["%%%", "a%b", "a_b", "50%"])
def test_director_like_escapes_user_wildcards(term):
    clause = func.array_to_string(Movie.directors, "|").icontains(term, autoescape=True)
    sql = str(clause.compile(compile_kwargs={"literal_binds": True}))
    assert "ESCAPE" in sql, (
        "sin ESCAPE, un % del usuario es un comodín y el ILIKE casa con todo"
    )


if __name__ == "__main__":
    test_keeps_matches_above_the_threshold()
    test_says_nothing_when_nothing_is_close()
    test_stops_at_the_first_row_below_the_threshold()
    test_threshold_sits_between_the_two_measured_populations()
    test_year_none_does_not_fabricate_a_release_date()
    for _t in ["%%%", "a%b", "a_b", "50%"]:
        test_director_like_escapes_user_wildcards(_t)
    print("ok")
