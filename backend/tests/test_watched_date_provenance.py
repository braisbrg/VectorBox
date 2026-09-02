"""`watched_date` significa UNA cosa: el día que viste la película.

Letterboxd mete tres fechas distintas en la misma columna del export —
`ratings.csv` trae el día que puntuaste, `watched.csv` el día que la marcaste, y
sólo `diary.csv` el día que la viste. Guardar las tres en el mismo campo sellaba
cientos de filas con el día del backfill (medido: 121 películas el 2023-01-22
para un usuario, el 38% de su biblioteca) e inventaba hábitos que no existían.

Ahora las otras dos no se escriben. Si un día vuelven, estos tests caen.
"""
import pandas as pd

from services.data_processor import DataProcessor


def test_ratings_csv_never_writes_a_date():
    """Su 'Date' es cuándo puntuaste. Sin diario no sabemos cuándo la viste, y
    NULL dice justo eso — mientras que una fecha inventada no se distingue de una
    real en ninguna consulta posterior."""
    movies_map = {}
    DataProcessor._process_ratings(
        pd.DataFrame([{"Name": "Parasite", "Year": 2019, "Rating": 4.5,
                       "Date": "2020-01-31", "Letterboxd URI": "https://boxd.it/a"}]),
        movies_map,
    )
    row = movies_map[next(iter(movies_map))]
    assert row["watched_date"] is None
    assert row["is_watched"] is True          # sí la viste; no sabemos cuándo


def test_watched_csv_never_writes_a_date():
    movies_map = {}
    DataProcessor._process_watched(
        pd.DataFrame([{"Name": "Solaris", "Year": 1972, "Date": "2020-01-31",
                       "Letterboxd URI": "https://boxd.it/c"}]),
        movies_map,
    )
    assert movies_map[next(iter(movies_map))]["watched_date"] is None


def test_the_diary_is_the_only_source_of_a_date():
    movies_map = {}
    DataProcessor._process_ratings(
        pd.DataFrame([{"Name": "Parasite", "Year": 2019, "Rating": 4.5,
                       "Date": "2020-01-31", "Letterboxd URI": "https://boxd.it/a"}]),
        movies_map,
    )
    key = next(iter(movies_map))
    assert movies_map[key]["watched_date"] is None

    DataProcessor._process_diary(
        pd.DataFrame([{"Name": "Parasite", "Year": 2019, "Rating": 4.5,
                       "Watched Date": "2019-11-08", "Date": "2020-01-31",
                       "Letterboxd URI": "https://boxd.it/a"}]),
        movies_map,
    )
    # 2019, la real — no 2020, que es cuándo la registraste.
    assert movies_map[key]["watched_date"].year == 2019


def test_the_diary_does_not_fall_back_to_its_own_log_date():
    """diary.csv trae 'Watched Date' y 'Date'. Usar el segundo de reserva volvía a
    meter la fecha de registro por la puerta de atrás."""
    movies_map = {}
    DataProcessor._process_diary(
        pd.DataFrame([{"Name": "Sin fecha", "Year": 2001, "Date": "2020-01-31",
                       "Letterboxd URI": "https://boxd.it/d"}]),
        movies_map,
    )
    assert movies_map[next(iter(movies_map))]["watched_date"] is None


def test_latest_diary_entry_wins_between_real_viewings():
    """La regla de revisionados sobrevive: dos fechas de diario reales → la nueva."""
    movies_map = {}
    rows = [{"Name": "Heat", "Year": 1995, "Watched Date": d, "Date": d,
             "Letterboxd URI": "https://boxd.it/b"} for d in ("2021-03-01", "2024-06-09")]
    DataProcessor._process_diary(pd.DataFrame(rows), movies_map)
    key = next(iter(movies_map))
    assert movies_map[key]["watched_date"].year == 2024
    assert movies_map[key]["watch_count"] == 2
