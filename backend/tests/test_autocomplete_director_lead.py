"""
`_names_one_director` decide si el término nombra a UN director inequívocamente.

Antes eso servía para colar sus películas por delante en el autocompletado; desde
2026-08-04 sirve para mostrar su TARJETA aparte, y las películas vuelven a
ordenarse sólo por relevancia. El predicado es el mismo y sus dos mitades siguen
siendo igual de necesarias: sin la de coincidencia única, "lee" mezcla a Lee
Unkrich con Spike Lee; sin la de prefijo de token, "eve" saca a St-eve-n
Spielberg. Ambas regresaron una vez en review, por eso están fijadas aquí.
"""
from routers.search import _names_one_director


def test_leads_when_term_names_one_director():
    rows = [["Akira Kurosawa"], ["Akira Kurosawa"], ["Akira Kurosawa"]]
    assert _names_one_director(rows, "kurosawa")
    # co-directed rows must not split the same person into two matches
    assert _names_one_director([["Ken Loach"], ["Ken Loach", "Rebecca O'Brien"]], "loach")


def test_does_not_lead_on_a_coincidental_substring():
    assert not _names_one_director([["Steven Spielberg"], ["Steven Spielberg"]], "eve")
    assert not _names_one_director([["Roman Polanski"], ["Miloš Forman"]], "man")
    assert not _names_one_director([["Lee Unkrich"], ["Spike Lee"]], "lee")
    assert not _names_one_director([], "kurosawa")
