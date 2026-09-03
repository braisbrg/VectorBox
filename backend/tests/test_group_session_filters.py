"""Filtros de año y calidad en la recomendación de grupo.

Lo que protege este fichero no es que los filtros filtren —eso lo comprueba
`scripts/audit_group_filters.py` contra datos reales— sino las dos decisiones que
son fáciles de deshacer sin notar nada:

1. **Los filtros locales van ANTES de las llamadas a TMDB.** `_build` pide
   proveedores por película, así que aplicar año y calidad dentro de él obliga a
   pagar una llamada de red por candidato descartado. Con el pool ampliado a 200
   eso son 200 llamadas para quedarse con 20.

2. **El pool se amplía sólo cuando hay filtros.** Medido el 2026-08-11: con 50
   candidatos, "2010+ y Q>=80 y menos de 90 min" sobrevivía **0 veces de 50** — la
   fila salía vacía y parecía que el grupo no tenía gustos comunes. Con 200 hay
   material. Sin filtros se piden 50 como siempre, porque el coste extra no compra
   nada y esta ruta ya es la más lenta del producto.
"""
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from routers.rss import GroupVibeRequest

FUENTE = (Path(__file__).resolve().parent.parent / "routers" / "rss.py").read_text(encoding="utf-8")


def test_accepts_the_two_new_filters():
    r = GroupVibeRequest(usernames=["a", "b"], year_min=1990, year_max=1999, min_score=85)
    assert (r.year_min, r.year_max, r.min_score) == (1990, 1999, 85)


def test_defaults_to_no_filtering():
    r = GroupVibeRequest(usernames=["a", "b"])
    assert r.year_min is None and r.year_max is None and r.min_score is None


@pytest.mark.parametrize("kwargs", [
    {"year_min": 1200},      # antes del cine
    {"year_max": 3000},
    {"min_score": 101},      # VBS va de 0 a 100
    {"min_score": -1},
])
def test_out_of_range_is_refused(kwargs):
    """Un 422 dice qué pasó; una lista vacía no dice nada."""
    with pytest.raises(ValidationError):
        GroupVibeRequest(usernames=["a", "b"], **kwargs)


def test_local_filters_run_before_the_network_calls():
    i_local = FUENTE.index("def _passes_local")
    i_build = FUENTE.index("async def _build")
    i_super = FUENTE.index("supervivientes = [")
    i_tasks = FUENTE.index("tasks = [_build(res)")
    assert i_local < i_build, "_passes_local se define antes de _build"
    assert i_super < i_tasks, (
        "hay que filtrar por lo local ANTES de construir, o se paga una llamada a "
        "TMDB por cada candidato que se va a descartar"
    )
    # Y `_build` no puede volver a decidir por año/calidad: si esas condiciones
    # siguen dentro, el filtrado barato de arriba no está haciendo su trabajo.
    cuerpo_build = FUENTE[i_build:i_tasks]
    for prohibido in ("payload.year_min", "payload.year_max", "payload.min_score"):
        assert prohibido not in cuerpo_build, f"{prohibido} sigue dentro de _build"


def test_the_candidate_pool_grows_only_when_there_are_filters():
    m = re.search(r"limit=(\d+) if hay_filtros else (\d+)", FUENTE)
    assert m, "el límite de candidatos ya no depende de si hay filtros"
    con, sin = int(m.group(1)), int(m.group(2))
    assert con > sin, "con filtros hacen falta MÁS candidatos, no menos"
    assert sin == 50, "sin filtros, el comportamiento de siempre"


def test_filters_reach_the_generator_not_just_the_post_filter():
    """EN ORIGEN, como las filas anchas del feed. Medido el 2026-08-11:

        filtro                 post-filtro   en origen
        2010+ Q>=80 90min        1/200         39/200
        los 90 + Q>=85           7/200         53/200

    Post-filtrando, la fusión gasta sus puestos en películas que se caen después.
    Si `session_filters` deja de llegar, esto vuelve a 1 de 200 sin que ningún test
    de los de arriba se entere: siguen pasando, porque el post-filtro sigue ahí.
    """
    assert "session_filters={" in FUENTE, "el endpoint ya no manda los filtros al generador"
    bloque = FUENTE[FUENTE.index("session_filters={"):]
    bloque = bloque[:bloque.index("} if hay_filtros else None")]
    for campo in ("year_min", "year_max", "min_score", "max_runtime"):
        assert campo in bloque, f"{campo} no viaja al generador"

    servicio = (Path(__file__).resolve().parent.parent / "services" / "rss_service.py").read_text(encoding="utf-8")
    assert "exclude=excluded_ids | no_elegibles" in servicio, (
        "la fusión tiene que excluir lo no elegible ANTES de rankear"
    )


def test_every_new_filter_counts_as_a_filter():
    """Si uno se queda fuera de la condición, ese caso vuelve a agotarse."""
    bloque = FUENTE[FUENTE.index("hay_filtros = bool("):]
    bloque = bloque[:bloque.index(")\n")]
    for campo in ("max_runtime", "providers", "year_min", "year_max", "min_score"):
        assert f"payload.{campo}" in bloque, f"{campo} no cuenta para ampliar el pool"


if __name__ == "__main__":
    for _n, _f in list(globals().items()):
        if _n.startswith("test_") and _n != "test_out_of_range_is_refused":
            _f()
    for _k in ({"year_min": 1200}, {"min_score": 101}):
        test_out_of_range_is_refused(_k)
    print("ok")
