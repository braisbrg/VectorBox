"""Matemática de los ejes de mood, sin Qdrant ni base de datos.

Lo que se protege aquí es lo que se rompe en silencio: un percentil mal escalado
o un eje montado con medio polo siguen produciendo números perfectamente creíbles.
"""
import numpy as np
import pytest

from services.mood_axes import (AXES, ANCHOR_GROUPS, CENTERING_ALPHA, QUADRANTS,
                                axis_directions, center, to_percentile, unit)


def test_centering_removes_the_shared_component():
    """Dos vectores al azar comparten una componente enorme y sin restarla
    ninguna dirección discrimina (en el catálogo real: coseno medio 0.482 -> 0.072
    con α=0.5).

    Se comprueba el MECANISMO, no un número: cuánto baja depende de lo dominante
    que sea la componente común, y en un fixture sintético eso lo elijo yo. Lo
    que tiene que cumplirse siempre es que restar más centroide acerque más a cero.
    """
    rng = np.random.default_rng(0)
    common = rng.normal(size=768)
    X = rng.normal(size=(400, 768)) * 0.35 + common
    mean_cos = lambda M: float(np.mean(M[:200] @ M[200:].T))

    before = mean_cos(unit(X))
    half = mean_cos(center(X, CENTERING_ALPHA))
    full = mean_cos(center(X, 1.0))

    assert before > 0.4                 # el fixture sí tiene el problema
    assert half < before                # α=0.5 lo reduce
    assert full < half                  # y restar más reduce más
    assert abs(full) < 0.10             # restar el centroide entero casi lo anula


def test_percentiles_span_the_full_range_in_order():
    p = to_percentile([0.5, -0.2, 0.9, 0.1])
    assert p.min() == 0.0 and p.max() == 100.0
    assert list(np.argsort(p)) == list(np.argsort([0.5, -0.2, 0.9, 0.1]))


def test_percentile_of_a_single_film_does_not_divide_by_zero():
    assert list(to_percentile([0.3])) == [0.0]


def test_an_axis_needs_both_poles():
    """Un eje con un polo vacío mediría 'cuánto se parece a X', no 'X frente a Y',
    y saldría igual de convincente. No se emite."""
    groups = {g: np.ones(8) for g in ANCHOR_GROUPS}
    assert set(axis_directions(groups)) == set(AXES)

    del groups["extrano"]          # único polo negativo de humanidad
    built = axis_directions(groups)
    assert "humanidad" not in built
    assert "gravedad" in built     # ésa no dependía de extrano


def test_direction_is_unit_and_flips_with_the_poles():
    rng = np.random.default_rng(1)
    groups = {g: rng.normal(size=64) for g in ANCHOR_GROUPS}
    d = axis_directions(groups)["gravedad"]
    assert np.isclose(np.linalg.norm(d), 1.0, atol=1e-5)

    swapped = dict(groups)
    for a, b in zip(AXES["gravedad"]["pos"], AXES["gravedad"]["neg"]):
        swapped[a], swapped[b] = groups[b], groups[a]
    # No es exactamente -d (los polos tienen distinto tamaño), pero el eje tiene
    # que apuntar al otro lado: si no, el signo no significa nada.
    assert float(axis_directions(swapped)["gravedad"] @ d) < 0


@pytest.mark.parametrize("axis,poles", list(AXES.items()))
def test_every_axis_references_real_anchor_groups(axis, poles):
    for group in list(poles["pos"]) + list(poles["neg"]):
        assert group in ANCHOR_GROUPS, f"{axis} apunta a un grupo inexistente: {group}"


def test_every_quadrant_key_is_one_qdrant_actually_filters_on():
    """El fallo que esto evita ya ocurrió una vez: un cuadrante declaraba claves
    que la búsqueda no miraba, así que el chip devolvía el feed sin tocar y sin
    un solo error. Una clave mal escrita aquí es indistinguible de no filtrar."""
    from services.qdrant_service import QdrantService
    used = {k for f in QUADRANTS.values() for k in f}
    assert used <= QdrantService.FILTER_KEYS, sorted(used - QdrantService.FILTER_KEYS)


def test_every_mood_payload_field_is_indexed():
    """Un filtro sin índice no falla: escanea el payload entero y sigue devolviendo
    lo correcto, sólo que lento. Este repo ya se comió exactamente eso — la
    búsqueda filtrada pasó de 6 ms a 160 ms y ningún test se enteró, porque la
    relevancia no cambiaba. Medido aquí: 27.7 ms sin índice contra 8-9 ms con él.

    Se comprueba sobre el fuente porque la lista de índices es una constante, no
    algo que se pueda preguntar sin un Qdrant vivo.
    """
    import re
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "services" / "qdrant_service.py").read_text(encoding="utf-8")
    block = src[src.index("indexes = ["): src.index("for field_name, field_schema in indexes")]
    indexed = set(re.findall(r'\("([a-z_]+)",\s*PayloadSchemaType', block))

    # mood_gravedad_min/-max apuntan al campo `mood_gravedad`, etc.
    targets = {k.rsplit("_", 1)[0] if k.rsplit("_", 1)[1] in ("min", "max") else k
               for f in QUADRANTS.values() for k in f}
    # Los suelos de votos/VBS viajan sobre campos que ya tienen su propio índice.
    alias = {"mood_min_votes": "vote_count", "mood_max_votes": "vote_count",
             "mood_min_vbs": "vectorbox_score"}
    needed = {alias.get(k, k) for k in targets}
    assert needed <= indexed, f"campos de mood sin índice de payload: {sorted(needed - indexed)}"


def test_popcorn_and_deep_cut_partition_the_same_mood_box():
    """Son la misma caja de ánimo partida por popularidad. Si los dos umbrales se
    separan, o los dos comparadores son inclusivos, aparecen películas en los dos
    chips (o en ninguno) y el corte deja de significar nada."""
    pop, deep = QUADRANTS["popcorn"], QUADRANTS["deep_cut"]
    assert pop["mood_gravedad_max"] == deep["mood_gravedad_max"]
    assert pop["mood_humanidad_max"] == deep["mood_humanidad_max"]
    # Mismo número en el corte: `min` es >= y `max` es <, así que no solapan.
    assert pop["mood_min_votes"] == deep["mood_max_votes"]


def test_no_anchor_group_is_too_small_to_be_a_direction():
    # Con menos de 4 películas el "grupo" lo define un puñado de casos y deja de
    # ser una dirección. El script las descarta; aquí se evita llegar a eso.
    for group, titles in ANCHOR_GROUPS.items():
        assert len(titles) >= 4, f"{group} tiene solo {len(titles)} anclas"
