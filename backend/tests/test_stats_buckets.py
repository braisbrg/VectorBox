"""Stats distributions — /stats renders whatever this returns, and a wrong
bucket still draws a perfectly convincing bar. The rewatch case is the one
that actually bit: total hours must count them, average runtime must not.
"""
from datetime import datetime

from routers.users import stats_buckets, GEM_IMDB_VOTES


def row(rating=None, date=None, plays=1, year=1999, runtime=100, genres=("Drama",),
        lang="en", directors=("Someone",), title="A Film", tmdb_id=1,
        imdb_rating=None, imdb_votes=None, countries=("USA",), cast=("An Actor",),
        grav=None, hum=None):
    # None passes through — NULL array columns are exactly what the last test asserts on.
    return (rating, date, plays, year, runtime,
            list(genres) if genres else None, lang,
            list(directors) if directors else None, title, tmdb_id,
            imdb_rating, imdb_votes, list(countries) if countries else None,
            list(cast) if cast else None, grav, hum)


def test_histogram_keeps_all_ten_buckets_and_counts_only_rated():
    out = stats_buckets([row(rating=4.5), row(rating=4.5), row(rating=None)])
    assert out["films"] == 3
    assert out["rated_films"] == 2
    assert len(out["rating_histogram"]) == 10           # stable x-axis even at zero
    by_star = {b["stars"]: b["films"] for b in out["rating_histogram"]}
    assert by_star[4.5] == 2
    assert by_star[0.5] == 0


def test_rewatches_count_as_hours_but_not_as_average_runtime():
    # One 120-min film seen 3 times: 6 hours in front of a screen, still a 120-min film.
    out = stats_buckets([row(runtime=120, plays=3)])
    assert out["runtime"]["total_hours"] == 6
    assert out["runtime"]["avg_minutes"] == 120


def test_per_year_only_counts_dated_rows_and_reports_the_coverage():
    out = stats_buckets([
        row(date=datetime(2024, 5, 1)),
        row(date=datetime(2024, 9, 1)),
        row(date=None),                                  # ratings.csv row, no date
    ])
    assert out["per_year"] == [{"year": 2024, "films": 2}]
    assert out["dated_films"] == 2 and out["films"] == 3  # the UI needs both to be honest


def test_decades_floor_and_facets_rank_by_count():
    out = stats_buckets([
        row(year=1994, genres=("Drama", "Crime"), directors=("A",)),
        row(year=1999, genres=("Drama",), directors=("B",)),
        row(year=2001, genres=("Crime",), directors=("A",)),
    ])
    assert out["decades"] == [{"decade": 1990, "films": 2}, {"decade": 2000, "films": 1}]
    assert out["genres"][0] == {"name": "Drama", "films": 2}
    assert out["directors"][0] == {"name": "A", "films": 2}


def test_empty_library_does_not_divide_by_zero():
    out = stats_buckets([])
    assert out["films"] == 0
    assert out["runtime"] == {"total_hours": 0, "avg_minutes": 0}
    assert out["per_year"] == [] and out["genres"] == []


def test_missing_metadata_is_skipped_not_bucketed():
    # Catalogue rows with NULL runtime/year/genres exist; they must not become a 0s decade.
    out = stats_buckets([row(year=None, runtime=None, genres=None, lang=None, directors=None,
                             countries=None)])
    assert out["decades"] == [] and out["genres"] == [] and out["directors"] == []
    assert out["countries"] == []
    assert out["runtime"]["avg_minutes"] == 0


def test_vs_crowd_doubles_stars_before_comparing_to_imdb():
    # 4★ IS 8/10. Without the ×2 every user reads as a savage critic.
    out = stats_buckets([row(rating=4.0, imdb_rating=8.0)])
    assert out["vs_crowd"]["delta"] == 0.0
    out = stats_buckets([row(rating=4.5, imdb_rating=7.0)])   # you: 9.0, crowd: 7.0
    assert out["vs_crowd"]["delta"] == 2.0
    assert out["vs_crowd"]["above"][0]["delta"] == 2.0


def test_vs_crowd_denominator_is_films_having_both_scores():
    out = stats_buckets([
        row(rating=4.0, imdb_rating=8.0),
        row(rating=4.0, imdb_rating=None),   # no crowd score
        row(rating=None, imdb_rating=8.0),   # you never rated it
    ])
    assert out["vs_crowd"]["films"] == 1     # never 3 — that would fake the average
    assert out["films"] == 3


def test_obscurity_uses_median_not_mean():
    # One blockbuster must not drag the whole library's typical film with it.
    votes = [1_000, 2_000, 3_000, 4_000, 5_000_000]
    out = stats_buckets([row(imdb_votes=v) for v in votes])
    assert out["obscurity"]["median_votes"] == 3_000
    assert out["obscurity"]["obscure_share"] == 0.8   # 4 of 5 under the gem threshold


def test_decade_ratings_need_three_films_and_are_separate_from_counts():
    out = stats_buckets(
        [row(year=1994, rating=5.0)]                                  # 1 film — no average
        + [row(year=2001, rating=r) for r in (3.0, 4.0, 5.0)]         # 3 films — averaged
    )
    assert out["decade_ratings"] == [{"decade": 2000, "avg": 4.0, "films": 3}]
    # ...while the raw count chart still shows the 1990s film.
    assert {"decade": 1990, "films": 1} in out["decades"]


def test_runtime_bands_are_exclusive_at_the_boundary():
    out = stats_buckets([row(runtime=r) for r in (89, 90, 120, 150, 240)])
    assert {b["name"]: b["films"] for b in out["runtime_bands"]} == {
        "<90": 1, "90–120": 1, "120–150": 1, "150+": 2,
    }


def test_rewatches_count_extra_plays_not_total_plays():
    out = stats_buckets([row(plays=3, title="Heat"), row(plays=1)])
    assert out["rewatches"]["films"] == 1        # only one film was rewatched
    assert out["rewatches"]["extra_plays"] == 2  # 3 plays = 2 rewatches
    assert out["rewatches"]["top"][0]["title"] == "Heat"


def test_weekday_is_empty_without_a_diary():
    # 10 of 12 real libraries have zero watched_date — the chart must not render
    # seven confident zeroes as if Monday were a real reading.
    assert stats_buckets([row(date=None)])["weekday"] == []
    out = stats_buckets([row(date=datetime(2024, 5, 6))])   # a Monday
    assert out["weekday"][0] == {"day": 0, "films": 1}


def test_a_marathon_day_is_no_longer_thrown_away():
    """Hubo un filtro que descartaba los días con >10 películas, porque Letterboxd
    metía tres fechas distintas en la misma columna y un backfill sellaba cientos
    de filas con un día. Ahora esas fechas ya no se escriben, así que la que llega
    es real — y un maratón de 12 películas es un dato, no un artefacto.

    El filtro se equivocaba justo aquí, y por eso se borró en vez de afinarlo."""
    day = datetime(2024, 5, 6)               # un lunes
    out = stats_buckets([row(date=day, tmdb_id=i) for i in range(12)])
    assert out["diary_films"] == 12
    assert out["per_year"] == [{"year": 2024, "films": 12}]
    assert out["weekday"][0]["films"] == 12


def test_dated_and_diary_are_the_same_number_now():
    """Se mantienen los dos campos por contrato con el frontend, pero ya no puede
    haber una fecha que exista y no sea real: si divergen, algo volvió a escribir
    fechas de log."""
    out = stats_buckets([
        row(date=datetime(2024, 1, 2)),
        row(date=None),
    ])
    assert out["dated_films"] == out["diary_films"] == 1
    assert out["films"] == 2


def test_actors_come_from_top_billed_cast():
    out = stats_buckets([
        row(cast=("Toshiro Mifune", "Takashi Shimura")),
        row(cast=("Toshiro Mifune",)),
        row(cast=None),
    ])
    assert out["actors"][0] == {"name": "Toshiro Mifune", "films": 2}
