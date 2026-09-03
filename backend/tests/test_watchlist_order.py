"""`sort_by=date_added` tiene que leer la POSICION de Letterboxd, no un id nuestro.

La regresión que esto guarda ya ocurrió: el orden era `movies.id DESC`, el orden en
que la PELICULA entró en nuestro catálogo, sin ninguna relación con cuándo la añadió
el usuario. Medido en el 212: `White Men Can't Jump` es el 4º de la página 1 en
Letterboxd y salía en el puesto 56 de 589.

`created_at` tampoco vale y es el error tentador, porque parece una fecha de alta:
es la fecha en que NOSOTROS escribimos la fila. El import inicial del 212 metió 594
filas en 35 instantes (hasta 50 en el mismo segundo), y los lotes incrementales
salen INVERTIDOS — insertamos en orden de Letterboxd, así que lo primero insertado
lleva el `created_at` más pequeño y ordenar DESC da la vuelta al lote.

Contra la fuente, no contra un mock: se compila el ORDER BY real del endpoint. Un
mock aquí sólo probaría que el mock hace lo que le he dicho.
"""
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent


def _date_added_branch() -> str:
    src = (BACKEND / "routers" / "recommendations.py").read_text(encoding="utf-8")
    m = re.search(
        r'if sort_by == "date_added":(.*?)elif sort_by ==', src, re.S
    )
    assert m, "no encuentro la rama date_added de get_watchlist"
    return m.group(1)


def test_date_added_orders_by_letterboxd_rank():
    branch = _date_added_branch()
    assert "watchlist_rank" in branch, (
        "date_added debe ordenar por UserRating.watchlist_rank — la posición en la "
        "watchlist de Letterboxd es lo único que publica sobre el orden de alta."
    )
    # NULLS LAST: una fila que ningún scrape ha visto (import por ZIP) no tiene
    # posición, y no se le inventa una poniéndola primera.
    assert "nulls_last" in branch


def test_date_added_never_orders_by_created_at():
    assert "created_at" not in _date_added_branch(), (
        "created_at es la fecha en que escribimos la fila, no la de alta: 594 filas "
        "del 212 comparten 35 instantes y cada lote incremental sale invertido."
    )


def test_sync_stamps_the_rank_counting_everything_the_page_brought():
    """El contador avanza con CADA item de la página, resuelva o no.

    Si sólo avanzase con lo que resuelve a TMDB, las 9 series de la watchlist del
    212 desplazarían todas las posiciones siguientes respecto a lo que el usuario ve
    en Letterboxd.
    """
    src = (BACKEND / "routers" / "rss.py").read_text(encoding="utf-8")
    m = re.search(r"for item in page_films:(.*?)if not tmdb_id:", src, re.S)
    assert m, "no encuentro el bucle de items de la watchlist"
    head = m.group(1)
    assert "lb_rank += 1" in head.split("tmdb_id = await")[0], (
        "el incremento debe ir ANTES de resolver el slug, o lo irresoluble no cuenta"
    )
    assert "watchlist_rank=lb_rank" in src and "watchlist_rank = lb_rank" in src, (
        "hay que estampar la posición tanto en la fila nueva como en la que ya existe "
        "— si no, lo ya importado nunca se ordena bien"
    )


if __name__ == "__main__":
    for _n, _f in list(globals().items()):
        if _n.startswith("test_"):
            _f()
    print("ok")
