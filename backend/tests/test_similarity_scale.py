"""Las DOS escalas de parecido: que ninguna sature ni se hunda.

`normalize_similarity_score` no tenía ni un test — `test_scoring.py`, el nombre que
parecía cubrirlo, es de VBS. Por eso una curva que convertía el 34% de los vecinos
en un 99 sobrevivió: nadie miró el reparto, sólo que el número saliera.

Datos reales medidos 2026-08-10 (10 semillas, sus 200 vecinos, 120 pares al azar):

    pares al azar     media 0.473   p99 0.685   max 0.694
    vecinos           p05  0.698   p50 0.769   p95 0.832   mejor 0.853
    The Matrix top-20 0.776 .. 0.827   -> antes: 15 de 20 salían 99
"""
import pytest

from utils.scoring import (
    FILM_NEIGH_P50,
    FILM_NEIGH_TOP,
    FILM_NOISE_P50,
    FILM_NOISE_TOP,
    normalize_film_similarity_score,
    normalize_similarity_score,
)

# Cosenos medidos, no inventados.
RUIDO = [0.397, 0.473, 0.550, 0.645, 0.685, 0.694]
VECINOS_MATRIX = [0.776, 0.796, 0.827]
VECINOS_TODOS = [0.690, 0.698, 0.711, 0.740, 0.769, 0.802, 0.832, 0.853]


def test_bounded_to_the_display_range():
    for c in [-1.0, 0.0, 0.3] + RUIDO + VECINOS_TODOS + [0.9, 1.0]:
        assert 60.0 <= normalize_film_similarity_score(c) <= 99.0


def test_monotone_non_decreasing():
    xs = [i / 200 for i in range(201)]
    ys = [normalize_film_similarity_score(x) for x in xs]
    assert all(b >= a for a, b in zip(ys, ys[1:])), "un parecido mayor no puede puntuar menos"


def test_noise_floors_at_60():
    # Dos películas sin relación puntúan 0.473 de media: eso es el suelo, no un 60
    # simbólico. Nada por debajo debe distinguirse, porque no hay nada que distinguir.
    assert normalize_film_similarity_score(FILM_NOISE_P50) == 60.0
    assert normalize_film_similarity_score(0.30) == 60.0
    assert normalize_film_similarity_score(-1.0) == 60.0


def test_the_two_populations_land_in_different_bands():
    """El techo del ruido y el vecino más flojo tienen que separarse de verdad."""
    techo_ruido = normalize_film_similarity_score(FILM_NOISE_TOP)      # 0.694 max observado
    peor_vecino = normalize_film_similarity_score(0.698)          # p05 de vecinos reales
    mediano = normalize_film_similarity_score(FILM_NEIGH_P50)
    assert techo_ruido == pytest.approx(75.0, abs=0.5)
    assert peor_vecino > techo_ruido
    assert mediano == pytest.approx(88.0, abs=0.5)


def test_a_real_row_does_not_collapse_into_nines():
    """LA regresión: The Matrix daba 15 nueves de 20.

    Su top-20 va de 0.776 a 0.827, un rango estrecho pero real, y la fila tiene que
    reflejarlo en vez de aplanarlo.
    """
    puntos = [round(normalize_film_similarity_score(c)) for c in VECINOS_MATRIX]
    assert len(set(puntos)) == len(puntos), f"la fila se aplana: {puntos}"
    assert max(puntos) - min(puntos) >= 5, f"reparto demasiado plano: {puntos}"
    assert all(p < 99 for p in puntos), "0.827 no es mejor que todo lo observado"


def test_only_better_than_everything_observed_reaches_99():
    # 0.853 fue el mejor vecino de las 10 semillas; el 99 se reserva para ahí arriba.
    assert normalize_film_similarity_score(FILM_NEIGH_TOP) == pytest.approx(98.0, abs=0.5)
    assert normalize_film_similarity_score(0.90) == 99.0
    assert normalize_film_similarity_score(0.832) < 99.0, "el p95 de vecinos no es un 99"


def test_the_whole_neighbour_population_spreads_out():
    """De 0.690 a 0.853 la escala tiene que usar buena parte de su recorrido."""
    puntos = [normalize_film_similarity_score(c) for c in VECINOS_TODOS]
    assert max(puntos) - min(puntos) >= 20, f"recorrido usado: {puntos}"
    assert len(set(round(p) for p in puntos)) >= 7, f"valores distintos: {puntos}"


def test_anchors_stay_ordered():
    # Si alguien reordena las anclas, los tramos se cruzan y la curva deja de ser
    # monótona sin que ningún otro test lo note.
    assert FILM_NOISE_P50 < FILM_NOISE_TOP < FILM_NEIGH_P50 < FILM_NEIGH_TOP


# ── la escala de CONSULTAS, y el error que casi me cuela ─────────────────────
#
# Intenté servir las dos poblaciones con la curva de vecinos. Las filas del
# showcase se hundieron de ~85 de media a 60–65, con heist70/en a 60.1. El golden
# set dio el visto bueno (0.874, idéntico) porque mide ORDEN y esto es MAGNITUD.
#
# Este test era lo ÚNICO que lo vio. Entonces se creyó que el suelo de
# `MIN_MEAN_SCORE = 55` era la red de seguridad y esto sólo el aviso previo;
# resultó que ese suelo no podía dispararse nunca (la escala tiene su propio
# suelo en 60) y se borró el 2026-08-11. Así que aquí no hay red debajo: si estas
# aserciones pasan estando la escala rota, nadie más lo nota.
#
# Cosenos consulta→película medidos sobre las filas reales del showcase, cuando
# esas filas puntuaban ~85 de media.
COSENOS_CONSULTA = [0.478, 0.524, 0.614, 0.643]
MEDIA_SANA = 75   # 10 puntos por debajo de lo observado sano; 60-65 era la rotura
MINIMO_SANO = 65


def test_query_scale_keeps_showcase_rows_where_they_were_measured():
    puntos = [normalize_similarity_score(c) for c in COSENOS_CONSULTA]
    media = sum(puntos) / len(puntos)
    assert media > MEDIA_SANA, (
        f"las filas del showcase caen a {media:.1f} de media (sanas: ~85); "
        f"a este nivel la escala está comprimida y nadie más lo comprueba: {puntos}"
    )
    assert min(puntos) > MINIMO_SANO


def test_the_two_scales_disagree_on_purpose():
    """El mismo coseno significa cosas distintas según qué se compare.

    Si alguien unifica las dos funciones, este test cae — y es la única señal de
    que se está rompiendo un extremo o el otro.
    """
    consulta = normalize_similarity_score(0.60)
    pelicula = normalize_film_similarity_score(0.60)
    assert consulta > pelicula + 10, (
        "0.60 es un buen resultado para una consulta y ruido entre dos películas"
    )
    # Y al revés en la punta: 0.80 es un vecino excelente, y como consulta se sale.
    assert normalize_similarity_score(0.80) == 99.0
    assert normalize_film_similarity_score(0.80) < 99.0


if __name__ == "__main__":
    # docker compose exec backend python -m tests.test_similarity_scale
    # (con -m; por ruta suelta falla al importar `utils`, que no es cosa de aquí)
    #
    # Recorrido, no lista a mano: la lista se quedó llamando a un test renombrado
    # y sólo fallaba al ejecutar el fichero directamente, porque pytest no mira
    # aquí. Ninguno lleva parámetros ni fixtures, así que basta con recorrerlos.
    for nombre, fn in sorted(globals().items()):
        if nombre.startswith("test_") and callable(fn):
            fn()
    print("ok")
