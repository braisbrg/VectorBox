"""Las escalas de parecido. DOS, porque hay dos poblaciones de cosenos.

Esto es lo que hay que leer antes de tocar nada aquí (medido 2026-08-10):

    consulta → película   0.48 – 0.62     Magic Box: un texto embebido contra el
                                          catálogo. Nunca se acerca mucho.
    película → película   0.69 – 0.85     fila "más como esto": dos vectores del
                                          mismo tipo, con ruido en 0.47.

Un 0.60 es "muy relevante" para una consulta y "no tiene nada que ver" para un par
de películas. Una sola recta no puede servir a las dos: la que estaba calibrada
para consultas hacía que el 34% de los vecinos de una película saliera 99 (The
Matrix 15 de 20, Blade Runner y Come and See 20 de 20), y al recalibrarla para
vecinos las filas del showcase se hundieron de ~85 de media a 60–65, con
`heist70/en` a 60.1. El golden set no lo vio porque mide ORDEN, no MAGNITUD; lo
vio `tests/test_similarity_scale.py`, que es la única comprobación que queda
sobre la magnitud desde que se borró el suelo del showcase (no podía dispararse:
leía esta escala, cuyo mínimo es 60, contra un umbral de 55).

Dos funciones no son dos significados: un 88 sigue queriendo decir "claramente por
encima del ruido de SU población". Es la recta única la que mentía en un extremo o
en el otro.
"""

# ── consulta → película ──────────────────────────────────────────────────────
# Anclas históricas, NO medidas por percentiles. Se conservan tal cual porque son
# las que tienen calibrado encima el golden set (nDCG 0.874) y las filas reales
# del showcase (~85 de media). Re-anclar esto exige medir la población
# consulta→película y volver a validar AMBOS.
#
# Ojo con el suelo de 60: es el que dejó muerto el umbral de confianza del
# showcase. Un número que nunca baja de 60 no puede cruzar ningún mínimo por
# debajo de 60.
QUERY_MIN_SIM = 0.2
QUERY_MAX_SIM = 0.7


def normalize_similarity_score(score: float) -> float:
    """Coseno consulta→película → escala 60–99.

      > 0.7      → 90–99   (interpolado, techo en 99)
      0.2 – 0.7  → 60–90   (lineal)
      < 0.2      → 60      (suelo)
    """
    if score > QUERY_MAX_SIM:
        return min(99.0, 90.0 + (score - QUERY_MAX_SIM) * 100)

    normalized = max(0.0, min(1.0, (score - QUERY_MIN_SIM) / (QUERY_MAX_SIM - QUERY_MIN_SIM)))
    return 60.0 + normalized * 30.0


# ── película → película ──────────────────────────────────────────────────────
# Anclas MEDIDAS, no números redondos (2026-08-10):
#
#     120 pares INDEPENDIENTES al azar   media 0.473  p99 0.685  max 0.694
#     200 vecinos reales (top-20)        p05  0.698  p50 0.769  p95 0.832  mejor 0.853
#
# Las dos poblaciones se separan limpiamente: 0/120 pares al azar llegan a 0.70. Y
# se TOCAN en 0.694/0.698, así que el corte va entre ambos y no en el 0.70 redondo
# — con 0.70 el peor vecino real caía del lado del ruido.
#
# Los pares al azar se miden con dos películas distintas cada vez. La primera
# pasada colgó los 30 pares de una sola película base y dio media 0.561 con máximo
# 0.817: medía lo hub que era esa película, no el suelo del espacio.
FILM_NOISE_P50 = 0.47
FILM_NOISE_TOP = 0.695
FILM_NEIGH_P50 = 0.77
FILM_NEIGH_TOP = 0.85


def normalize_film_similarity_score(score: float) -> float:
    """Coseno película→película → escala 60–99, a trozos por percentiles.

      ≤ 0.47        → 60        ruido puro
      0.47 – 0.695  → 60–75     dentro del ruido
      0.695 – 0.77  → 75–88     vecino real, de flojo a mediano
      0.77 – 0.85   → 88–98     donde vive el 50% bueno, con sitio para separarse
      > 0.85        → 99        mejor que cualquier vecino observado

    A trozos y por percentiles igual que VBS v2, que es el patrón que este repo ya
    usa para lo mismo: estirar la escala donde está la población.
    """
    if score <= FILM_NOISE_P50:
        return 60.0
    if score <= FILM_NOISE_TOP:
        return 60.0 + (score - FILM_NOISE_P50) / (FILM_NOISE_TOP - FILM_NOISE_P50) * 15.0
    if score <= FILM_NEIGH_P50:
        return 75.0 + (score - FILM_NOISE_TOP) / (FILM_NEIGH_P50 - FILM_NOISE_TOP) * 13.0
    if score <= FILM_NEIGH_TOP:
        return 88.0 + (score - FILM_NEIGH_P50) / (FILM_NEIGH_TOP - FILM_NEIGH_P50) * 10.0
    return 99.0
