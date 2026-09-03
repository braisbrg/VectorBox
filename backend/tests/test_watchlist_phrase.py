"""«de mi lista» en el Magic Box: detección por texto, sin Groq y sin esquema.

La alternativa era un campo más en `MovieSearchIntent`, y en este repo eso ya se
pagó: `original_language` se lo inventaba 6 de 10 veces (CLAUDE.md). Aquí una
alucinación no degrada el resultado, lo VACÍA — recorta el universo al 4% del
catálogo. Determinista, cero tokens, y por eso se puede probar aquí mismo.

Los dos riesgos son simétricos y los dos están cubiertos abajo: no disparar
cuando se pide (la consulta devuelve el catálogo entero fingiendo que filtró) y
disparar cuando no (Schindler's List te devuelve tu watchlist).
"""
import pytest

from services.nlp_search import detectar_watchlist


@pytest.mark.parametrize("frase,esperado", [
    ("algo corto de mi lista", "algo corto"),
    ("películas raras de mi watchlist", "películas raras"),
    ("from my watchlist, something funny", "something funny"),
    ("sci-fi in my list", "sci-fi"),
    ("algo de mi lista de pendientes", "algo"),
    ("como Memento de mi lista", "como Memento"),
])
def test_detecta_y_recorta_la_frase(frase, esperado):
    """Se QUITA del texto: lo que queda se embebe, y "de mi lista" no describe
    ninguna película — dejarlo dentro mete tokens de lista en un vector de tema."""
    consulta, pide = detectar_watchlist(frase)
    assert pide is True
    assert consulta == esperado


@pytest.mark.parametrize("frase", [
    "películas sobre la lista de Schindler",   # LA lista, no MI lista
    "a movie about my list of regrets",
    "my list of favourite films",              # sin preposición delante
    "algo reconfortante",
])
def test_no_dispara_de_mas(frase):
    consulta, pide = detectar_watchlist(frase)
    assert pide is False
    assert consulta == frase                   # y no toca el texto


def test_solo_el_ambito_no_deja_consulta():
    """Sin tema que buscar no se marca: para eso está el conmutador. Marcarlo
    dejaría una búsqueda semántica sin sujeto sobre el 4% del catálogo."""
    assert detectar_watchlist("de mi lista") == ("de mi lista", False)
