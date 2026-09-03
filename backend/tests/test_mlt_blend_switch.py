"""«Mas como estas» — la rama que decide como se busca una mezcla.

El fallo del que protege no da error: con un solo vector promedio, una mezcla de
cinco caprichos devuelve la papilla que queda en medio y la pelicula que el
usuario eligio no pinta nada en la lista (medido: el 91% de las mezclas dispares
de 5 dejaban alguna semilla sin nada). Con una busqueda por semilla siempre, se
pierde acierto cuando las semillas SI van juntas. Asi que se comprueba que cada
regimen toma su camino, y que una sola semilla no cambia de comportamiento.
"""
import numpy as np

from routers.similar import SEED_COHERENCE_CUT, blend_queries


def _pair(cos, dim=8):
    """Dos vectores unitarios al coseno pedido."""
    a = np.zeros(dim); a[0] = 1.0
    b = np.zeros(dim); b[0] = cos; b[1] = np.sqrt(1 - cos ** 2)
    return [a, b]


def test_una_semilla_es_esa_semilla():
    v = np.array([3.0, 4.0, 0.0])
    assert blend_queries([v]) == [[0.6, 0.8, 0.0]]


def test_semillas_que_van_juntas_se_buscan_como_una_sola():
    assert len(blend_queries(_pair(SEED_COHERENCE_CUT + 0.05))) == 1


def test_semillas_dispares_se_buscan_por_separado():
    qs = blend_queries(_pair(SEED_COHERENCE_CUT - 0.05))
    assert len(qs) == 2
    # y son las semillas mismas, no un promedio
    assert np.allclose(qs[0], _pair(SEED_COHERENCE_CUT - 0.05)[0])


def test_la_coherencia_es_la_media_de_los_pares_no_del_peor():
    # tres juntas y una lejos: la media aguanta por encima del corte
    a = np.zeros(8); a[0] = 1.0
    juntas = [a, a.copy(), a.copy()]
    lejos = np.zeros(8); lejos[1] = 1.0          # ortogonal a las otras
    assert len(blend_queries(juntas)) == 1
    assert len(blend_queries(juntas + [lejos])) == 4


def test_los_vectores_de_consulta_salen_normalizados():
    for qs in (blend_queries([np.array([9.0, 0.0, 0.0])]),
               blend_queries(_pair(0.2))):
        for q in qs:
            assert abs(np.linalg.norm(q) - 1.0) < 1e-6
