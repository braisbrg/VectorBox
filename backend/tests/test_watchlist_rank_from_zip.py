"""El ZIP trae la fecha de alta; hay que convertirla en posición y no tirarla.

`watchlist.csv` es el único sitio donde Letterboxd publica CUANDO se añadió cada
película — la web sólo da el orden de la página. El parser leía título, año y URI
de ese fichero e ignoraba la columna `Date`, así que un usuario que importa por ZIP
y no vincula su perfil se quedaba sin orden posible.

Se guarda como posición y no como fecha para que ZIP y scrape escriban la MISMA
columna: si no, el orden cambiaría al vincular el perfil.
"""
import pandas as pd

from services.data_processor import DataProcessor


def _csv(rows):
    return pd.DataFrame(rows, columns=["Date", "Name", "Year", "Letterboxd URI"])


def test_newest_addition_is_rank_one():
    # El export va de más antiguo a más nuevo; la lista se ve al contrario.
    df = _csv([
        ("2024-01-01", "Vieja", 1990, "https://letterboxd.com/film/vieja/"),
        ("2025-06-01", "Media", 2000, "https://letterboxd.com/film/media/"),
        ("2026-08-01", "Nueva", 2020, "https://letterboxd.com/film/nueva/"),
    ])
    ranks = DataProcessor._watchlist_ranks(df)
    assert [ranks[i] for i in df.index] == [3, 2, 1]


def test_same_day_breaks_by_file_position_last_first():
    # 'Date' es sólo el día, así que los empates son la norma, no el borde.
    df = _csv([
        ("2026-08-01", "Primera del dia", 1990, "u1"),
        ("2026-08-01", "Segunda del dia", 1991, "u2"),
        ("2026-08-01", "Tercera del dia", 1992, "u3"),
    ])
    ranks = DataProcessor._watchlist_ranks(df)
    assert [ranks[i] for i in df.index] == [3, 2, 1]


def test_missing_dates_go_last_and_keep_relative_order():
    df = _csv([
        (None, "Sin fecha A", 1990, "u1"),
        ("2026-08-01", "Con fecha", 2000, "u2"),
        ("", "Sin fecha B", 1991, "u3"),
    ])
    ranks = DataProcessor._watchlist_ranks(df)
    assert ranks[1] == 1, "la que tiene fecha va primera"
    assert {ranks[0], ranks[2]} == {2, 3}, "las sin fecha, al final"


def test_no_date_column_still_reverses_the_file():
    # Exports viejos sin columna 'Date': el orden del fichero es lo único que hay.
    df = pd.DataFrame(
        [("A", 1990, "u1"), ("B", 1991, "u2")], columns=["Name", "Year", "Letterboxd URI"]
    )
    assert [DataProcessor._watchlist_ranks(df)[i] for i in df.index] == [2, 1]


def test_ranks_reach_the_movies_map():
    """El parser tiene que dejar la posición donde el upsert la lee."""
    df = _csv([
        ("2024-01-01", "Vieja", 1990, "https://letterboxd.com/film/vieja/"),
        ("2026-08-01", "Nueva", 2020, "https://letterboxd.com/film/nueva/"),
    ])
    movies_map = {}
    DataProcessor._process_watchlist(df, movies_map)
    by_title = {v["title"]: v for v in movies_map.values()}
    assert by_title["Nueva"]["watchlist_rank"] == 1
    assert by_title["Vieja"]["watchlist_rank"] == 2


def test_rank_is_set_on_a_film_already_seen_by_another_csv():
    """Una película ya vista Y en la watchlist entra por ratings/watched primero."""
    df = _csv([("2026-08-01", "Revisitar", 1980, "https://letterboxd.com/film/revisitar/")])
    key = DataProcessor._get_key(df.iloc[0])
    movies_map = {key: {"title": "Revisitar", "is_watchlist": False, "is_watched": True}}
    DataProcessor._process_watchlist(df, movies_map)
    assert movies_map[key]["is_watchlist"] is True
    assert movies_map[key]["watchlist_rank"] == 1


if __name__ == "__main__":
    for _n, _f in list(globals().items()):
        if _n.startswith("test_"):
            _f()
    print("ok")
