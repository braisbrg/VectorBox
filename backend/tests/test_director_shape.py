"""Forma de una filmografía. El error que este módulo existe para no cometer es
calcularla sobre la página en vez de sobre la obra entera: las 50 más populares
de Scorsese dirían que su reparto habitual es DiCaprio, no De Niro.
"""
from routers.directors import director_shape, library_shape, MIN_FILMS_TO_COMPARE


# (tmdb_id, title, year, poster_path, score, rating, is_watched, is_watchlist, is_upcoming)
def lib(tmdb_id=1, title="A Film", year=1990, poster="/p.jpg", score=70.0,
        rating=None, watched=False, watchlist=False, upcoming=False):
    return (tmdb_id, title, year, poster, score, rating, watched, watchlist, upcoming)


def row(cast=(), genres=("Drama",), year=1990, score=70.0):
    return (list(cast), list(genres), year, score)


def test_recurring_cast_needs_two_films():
    out = director_shape([
        row(cast=("De Niro", "Keitel")),
        row(cast=("De Niro",)),
        row(cast=("Extra",)),          # una sola vez: no es recurrencia
    ])
    assert out["recurring_cast"] == [{"name": "De Niro", "films": 2}]


def test_the_director_is_not_their_own_recurring_collaborator():
    # Scorsese sale acreditado en sus propios documentales: 8 películas, primero
    # de la lista, y sin sentido en una ficha suya.
    out = director_shape(
        [row(cast=("Martin Scorsese", "De Niro")) for _ in range(3)],
        name="Martin Scorsese",
    )
    assert [a["name"] for a in out["recurring_cast"]] == ["De Niro"]


def test_span_and_avg_ignore_missing_values():
    out = director_shape([
        row(year=1976, score=80.0),
        row(year=None, score=None),
        row(year=1990, score=90.0),
    ])
    assert out["span"] == {"from": 1976, "to": 1990}
    assert out["avg_score"] == 85.0    # la fila sin nota no cuenta como 0


def test_empty_filmography_returns_nulls_not_zeroes():
    out = director_shape([])
    assert out["span"] is None
    assert out["avg_score"] is None    # 0.0 se leería como "malísimo"
    assert out["recurring_cast"] == [] and out["genres"] == []


def test_comparison_is_withheld_under_the_floor():
    """Con dos películas, «+1.2★ sobre tu media» es ruido. El conteo sale
    siempre; la comparación sólo desde MIN_FILMS_TO_COMPARE."""
    two = [lib(tmdb_id=i, rating=5.0, watched=True) for i in range(2)]
    out = library_shape(two, global_avg=3.5)
    assert out["seen"] == 2 and out["avg_rating"] == 5.0   # el dato crudo sí
    assert out["vs_your_avg"] is None                       # la lectura no

    enough = [lib(tmdb_id=i, rating=5.0, watched=True) for i in range(MIN_FILMS_TO_COMPARE)]
    assert library_shape(enough, global_avg=3.5)["vs_your_avg"] == 1.5


def test_best_unseen_ignores_what_you_have_already_watched():
    out = library_shape([
        lib(tmdb_id=1, title="Seen Masterpiece", score=95.0, watched=True, rating=5.0),
        lib(tmdb_id=2, title="Unseen Good", score=80.0),
        lib(tmdb_id=3, title="Unseen Lesser", score=60.0),
    ], global_avg=3.5)
    assert out["best_unseen"]["title"] == "Unseen Good"     # nunca la de 95 ya vista
    assert out["seen"] == 1


def test_best_unseen_never_recommends_an_unreleased_film():
    """Real: la mejor pendiente de Nolan salía «The Odyssey» (2026, Q 90.4).
    Es la de mayor Q sin ver y no se puede ver."""
    out = library_shape([
        lib(tmdb_id=1, title="The Odyssey", year=2026, score=90.4, upcoming=True),
        lib(tmdb_id=2, title="Insomnia", year=2002, score=75.0),
    ], global_avg=3.5)
    assert out["best_unseen"]["title"] == "Insomnia"


def test_watchlisted_excludes_films_already_seen():
    # Una película vista que quedó marcada en la watchlist no es un pendiente.
    out = library_shape([
        lib(tmdb_id=1, watched=True, watchlist=True),
        lib(tmdb_id=2, watchlist=True),
    ], global_avg=3.5)
    assert out["watchlisted"] == 1


def test_no_ratings_yields_no_average_rather_than_zero():
    out = library_shape([lib(watched=True, rating=None)], global_avg=3.5)
    assert out["seen"] == 1 and out["rated"] == 0
    assert out["avg_rating"] is None and out["vs_your_avg"] is None


def test_a_user_with_no_global_average_gets_no_comparison():
    # Usuario recién llegado: sin media global no hay nada contra lo que comparar.
    rows = [lib(tmdb_id=i, rating=4.0, watched=True) for i in range(5)]
    assert library_shape(rows, global_avg=None)["vs_your_avg"] is None
