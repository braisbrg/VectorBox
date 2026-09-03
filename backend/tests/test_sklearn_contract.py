"""El K-Means del clustering, protegido de una subida de scikit-learn.

## Por qué existe

El 2026-09-03 subió `scikit-learn` de 1.8.0 a 1.9.0 dentro del grupo
`infrastructure-critical`, y **los 517 tests pasaron sin tocar una sola vez el
K-Means**. Hubo que comprobarlo a mano en el contenedor. Lo que se comprueba a mano
una vez no protege nada la próxima: la subida siguiente vuelve a pasar en verde.

`sklearn` no es una dependencia cualquiera aquí — decide los clusters de gusto de cada
usuario, y sus nombres los escribe un LLM a partir de ellos. Una regresión ahí no falla:
reagrupa. Y un cambio de agrupación no se distingue de "el usuario ha visto pelis nuevas"
mirando la UI.

## Qué cubre, y qué NO

Cubre la **superficie de sklearn que este repo usa**: `KMeans(n_init=, max_iter=,
random_state=)`, `fit_predict(X, sample_weight=)` y `silhouette_score(..., sample_size=,
random_state=)`. Si cualquiera de esas firmas cambia o deja de aceptar un argumento, esto
se cae con un TypeError en vez de en producción.

NO cubre si los clusters son *buenos* — eso no lo puede ver un test. Cubre que sobre datos
con estructura CONOCIDA la función encuentra esa estructura, que es lo máximo que un test
hermético puede afirmar.
"""
import numpy as np
import pytest

from services.clustering_service import ClusteringService


def _blobs(k: int = 5, por_blob: int = 40, dim: int = 768, sep: float = 6.0):
    """k gaussianas bien separadas en el espacio del embedding real (768-dim).

    `sep=6.0` con `scale=0.4` deja los blobs sin solape, así que la respuesta correcta
    no es una opinión: son k. Semilla fija — si esto se vuelve inestable, el problema no
    es el test.
    """
    rng = np.random.default_rng(0)
    centros = rng.normal(size=(k, dim)) * sep
    return np.vstack([c + rng.normal(scale=0.4, size=(por_blob, dim)) for c in centros])


def test_encuentra_la_estructura_que_hay():
    """Con 5 blobs disjuntos, la silueta tiene que elegir 5.

    El barrido va de k=3 a k=10 (`k_max = min(12, n//20)`), así que acertar 5 no es
    suerte de un rango estrecho: hay ocho candidatos y elige el correcto.
    """
    X = _blobs(k=5)
    assert ClusteringService.calculate_optimal_clusters(200, X) == 5


def test_sample_weight_sigue_aceptandose():
    """La ruta con pesos es la de producción: las películas pesan por rating y recencia.

    Un cambio de firma en `fit_predict` la rompería sin que el camino sin pesos se
    enterase, y es el que usan de verdad los usuarios con historial fechado.
    """
    X = _blobs(k=5)
    pesos = np.ones(200)
    assert ClusteringService.calculate_optimal_clusters(200, X, pesos) == 5


@pytest.mark.parametrize("n_movies, esperado", [
    (100, 5),   # min(5, 100//20) -> 5
    (20, 2),    # max(2, 20//20=1) -> 2
    (60, 3),    # min(5, 3) -> 3
])
def test_sin_vectores_cae_a_la_formula_fija(n_movies, esperado):
    """Los llamantes que sólo conocen `n_movies` no pagan la silueta.

    Esta rama no toca sklearn en absoluto, y por eso mismo hay que fijarla: es el
    comportamiento que se mantiene si algún día la silueta se cae o se retira.
    """
    assert ClusteringService.calculate_optimal_clusters(n_movies) == esperado


def test_por_debajo_de_30_no_se_fia_de_la_silueta():
    """Con menos de 30 puntos la silueta no es fiable y la función lo sabe.

    Se le pasan vectores CON estructura y aun así ignora la silueta: el guardia va por
    `n_movies`, no por si hay datos. Si alguien lo "arregla" para que también use la
    silueta aquí, este test lo cuenta.
    """
    X = _blobs(k=5)
    assert ClusteringService.calculate_optimal_clusters(25, X[:25]) == 2
