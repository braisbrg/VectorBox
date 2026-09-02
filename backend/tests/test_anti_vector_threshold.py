"""A quién penaliza el anti-vector se decide por la LISTA, no por un coseno fijo.

Un coseno absoluto no significa lo mismo para dos usuarios en este espacio. Medido
el 2026-09-01 sobre los heads reales (30 candidatos) de los 10 usuarios con
anti-vector, con el umbral fijo que había:

    >0.80 (tirar)   → 0 de 30 para LOS DIEZ. El coseno más alto de cualquier head
                      fue 0.781, así que la rama no podía ejecutarse nunca.
    >0.65 (demotar) → de 0 (u329, u280) a 22 de 30 (u210).

Y demotar 22 de 30 con un factor uniforme no penaliza: conserva el orden dentro de
cada grupo, así que lo único que hacía era SUBIR a los 8 que escapaban — elegidos
por dónde caía la raya en una distribución sin hueco (los 8 entre 0.552 y 0.644,
los 22 justo por encima). El top-5 de u210 pasaba a ser cuatro de esos ocho.

Lo mismo, con los números a pelo, en el BYW de `recommendation_engine`: sobre los
vecinos reales de las anclas de 6 usuarios (84-100 candidatos), 0.80 no disparó ni
una vez y 0.65 tocaba del 11,0% al 61,9%.

Tras el cambio: 2-3 de 30 para los diez.

Con un precio, que está fijado abajo como test y no como nota al pie: la tasa es
CONSTANTE por construcción. Ver
`test_la_tasa_es_constante_por_construccion_ES_UNA_LIMITACION_CONOCIDA`.
"""
import ast
import re
from pathlib import Path

from utils.anti_vector import ANTI_SIMILARITY_DEMOTE_FRACTION, most_anti_similar

BACKEND = Path(__file__).resolve().parents[1]


def test_penalizar_es_la_excepcion_sea_cual_sea_la_escala():
    """La fracción se respeta, y no depende de DÓNDE esté el coseno."""
    apretada = {i: 0.60 + i * 0.001 for i in range(30)}   # sd minúscula, todo alto
    ancha = {i: 0.30 + i * 0.015 for i in range(30)}      # mismo orden, otro rango
    assert len(most_anti_similar(apretada)) == 3
    assert len(most_anti_similar(ancha)) == 3
    # Y elige a LOS MISMOS: sólo importa el orden dentro de la lista, no el valor.
    assert most_anti_similar(apretada) == most_anti_similar(ancha)


def test_desplazar_todos_los_cosenos_no_cambia_a_quien_se_penaliza():
    """La propiedad por la que existe el arreglo: invarianza al nivel absoluto.

    Un usuario con un conjunto negativo apretado tiene TODOS sus cosenos altos.
    Con umbral fijo eso le penalizaba media lista; aquí no cambia nada.
    """
    base = {i: c for i, c in enumerate([0.41, 0.55, 0.62, 0.48, 0.71, 0.33, 0.69, 0.52, 0.60, 0.44])}
    subida = {i: c + 0.20 for i, c in base.items()}
    assert most_anti_similar(base) == most_anti_similar(subida)


def test_nunca_penaliza_la_lista_entera_ni_devuelve_vacio_con_datos():
    assert most_anti_similar({}) == set()
    for n in (1, 2, 3, 5, 30, 100):
        sel = most_anti_similar({i: i / n for i in range(n)})
        assert 1 <= len(sel) < n or n == 1, f"n={n}: {len(sel)}"


def test_la_tasa_es_constante_por_construccion_ES_UNA_LIMITACION_CONOCIDA():
    """Penaliza el 10% SIEMPRE, haya aversión o no. No es un bug: es el trato.

    El arreglo cambió una varianza sin control (0%-73% de la lista según el
    usuario) por un sesgo conocido. Lo que se pierde es poder decir «aquí no hay
    nada parecido a lo que odias»: una lista entera benigna recibe la misma
    proporción de castigo que una llena de cosas que el usuario rechaza.

    El caso medido (2026-09-01): el head de u280 va de coseno 0.300 a 0.437 y en
    todo el catálogo sólo 3 de 21.405 películas le pasan de 0.65 — no hay nada
    que penalizar y se penalizan 2 de 20. Con el umbral viejo eran 0.

    Este test existe para que el día que alguien lo cambie sea A PROPÓSITO, y
    para que nadie lo abra como defecto. El camino a v2 —percentil contra la
    distribución del usuario sobre el catálogo, no contra la de la lista— está en
    el docstring de `most_anti_similar`.
    """
    sin_aversion = {i: 0.30 + i * 0.007 for i in range(20)}   # nada llega a 0.44
    con_aversion = {i: 0.55 + i * 0.012 for i in range(20)}   # la mitad pasa de 0.65
    assert len(most_anti_similar(sin_aversion)) == 2
    assert len(most_anti_similar(con_aversion)) == 2
    # Misma proporción en los dos casos: el valor absoluto no entra en la decisión.
    assert (len(most_anti_similar(sin_aversion)) / len(sin_aversion)
            == len(most_anti_similar(con_aversion)) / len(con_aversion))


def test_la_fraccion_es_una_fraccion():
    assert 0.0 < ANTI_SIMILARITY_DEMOTE_FRACTION < 0.5


def test_no_vuelve_ningun_umbral_absoluto_contra_el_anti_vector():
    """El coseno del anti-vector no se compara con una constante en ningún sitio.

    Sobre el AST, no sobre el texto: busca comparaciones cuyo lado izquierdo sea
    una variable de coseno y el derecho un número. `_filter_by_anti_vector`
    (drop duro en las filas de auteur/cult) queda FUERA a propósito — es un
    descarte, no un reordenado, y su selectividad no está medida todavía.
    """
    culpables = []
    for ruta in (BACKEND / "services").glob("*.py"):
        arbol = ast.parse(ruta.read_text(encoding="utf-8"), filename=str(ruta))
        for nodo in ast.walk(arbol):
            if not isinstance(nodo, ast.Compare):
                continue
            izq = nodo.left
            if not (isinstance(izq, ast.Name) and re.search(r"cos", izq.id, re.I)):
                continue
            for cmp_ in nodo.comparators:
                if isinstance(cmp_, ast.Constant) and isinstance(cmp_.value, float):
                    culpables.append(f"{ruta.name}:{nodo.lineno}  {izq.id} vs {cmp_.value}")
    culpables = [c for c in culpables if "drop_threshold" not in c]
    assert not culpables, (
        "un coseno absoluto no significa lo mismo para dos usuarios — usa "
        "most_anti_similar sobre la lista:\n  " + "\n  ".join(culpables)
    )
