"""La caché de /directors está PARTIDA: lo público se comparte, lo personal no.

La clave no lleva user_id — a propósito, si lo llevase no ahorraría nada. Eso la
hace segura sólo mientras el bloque `library` se inyecte DESPUÉS de leer la caché.
El día que alguien lo meta dentro de `_build_public`, dos usuarios compartirán
"has visto 25/47" y "la mejor que te falta", y no habrá ningún error: la respuesta
seguirá siendo un 200 perfectamente válido con los datos de otro.

Guard estático porque el fallo vive en la ESTRUCTURA del código, no en un valor.
"""
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
SRC = (BACKEND / "routers" / "directors.py").read_text(encoding="utf-8")


def _body(func_name: str) -> str:
    """Cuerpo de una función async top-level, hasta la siguiente def top-level."""
    start = SRC.index(f"async def {func_name}")
    rest = SRC[start:]
    nxt = re.search(r"\n(?:async def |def |@router)", rest[1:])
    return rest[: nxt.start() + 1] if nxt else rest


def test_the_cacheable_half_never_touches_user_data():
    body = _body("_build_public")
    for forbidden in ("library", "user_id", "current_user", "UserRating"):
        assert forbidden not in body, (
            f"_build_public menciona {forbidden!r}: lo que devuelve se guarda en una "
            f"clave compartida entre usuarios"
        )


def test_the_personal_half_is_never_written_to_the_cache():
    body = _body("director_films")
    # Lo que se serializa a Redis es `public`, nunca el dict final con library.
    assert "orjson.dumps(public)" in body
    assert "setex" in body
    # Y el inyectado ocurre en el return, fuera del camino de escritura.
    assert '{**public, "library": library}' in body


def test_the_cache_key_excludes_the_user_and_includes_what_changes_the_payload():
    body = _body("director_films")
    key = body[body.index("cache_key = "): body.index("public = None")]
    assert "user" not in key, "meter el usuario en la clave la vuelve inútil"
    # `needle`, no `name`: la clave va sobre el nombre PLEGADO, que es también lo
    # que busca la consulta. Cuando eran distintos (clave en `name.lower()`,
    # consulta exacta) las variantes compartían caché pero no resultado, y
    # "akira kurosawa" daba 404 en frío y 200 si "Akira Kurosawa" había pasado.
    for part in ("lang", "needle", "sort", "limit", "offset"):
        assert part in key, f"la clave ignora {part}: dos peticiones distintas colisionarían"


def test_folding_collapses_spellings_without_merging_people():
    """Lo que la clave y la consulta comparten: qué cuenta como el mismo nombre.

    La mitad de arriba es el bug (variantes que debían dar la misma ficha); la de
    abajo es lo que NO se puede romper al arreglarlo — "Anderson" no puede juntar
    a Wes, a Paul Thomas y a Brad, que es justo por lo que el match es exacto.
    """
    from routers.search import _normalize_needle as fold

    for variante in ("Akira Kurosawa", "akira kurosawa", "AKIRA KUROSAWA",
                     "Akira  Kurosawa", "  akira kurosawa  "):
        assert fold(variante) == fold("Akira Kurosawa"), variante
    assert fold("Achero Mañas") == fold("achero manas")

    assert fold("Wes Anderson") != fold("Paul Thomas Anderson")
    assert fold("Anderson") != fold("Wes Anderson")
