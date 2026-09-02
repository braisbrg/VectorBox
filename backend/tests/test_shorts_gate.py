"""El gate de cortos: qué deja pasar y, sobre todo, qué NO descarta.

El fallo caro aquí es tratar «duración desconocida» como corto. La primera
versión de esta función sólo contemplaba `runtime IS NULL` y se comía en
silencio las 505 filas con `runtime = 0` — que es el centinela que este
catálogo usa de verdad (0 filas con NULL, medido 2026-08-18). El test de
entonces pasaba: comprobaba que la cadena "IS NULL" apareciera en el SQL, cosa
que era cierta y no demostraba nada. De ahí que aquí se compile la cláusula con
sus literales y se afirme sobre los tres umbrales, en vez de sobre una
subcadena cualquiera.
"""
import pytest

from services.recommendation_engine import (
    movie_quality_gate,
    SHORT_FILM_MAX_RUNTIME,
    _MOVIE_QUALITY_GATE,
)


def _sql(clauses) -> str:
    return " ".join(str(c) for c in clauses)


def _runtime_clause_sql() -> str:
    """La cláusula de duración, compilada con sus valores literales dentro."""
    clause = movie_quality_gate()[-1]
    return str(clause.compile(compile_kwargs={"literal_binds": True}))


def test_por_defecto_excluye_cortos():
    assert "runtime" in _sql(movie_quality_gate()), "el gate por defecto debe filtrar por duración"


def test_la_duracion_desconocida_no_cuenta_como_corto():
    """Las dos formas de «no se sabe» deben seguir entrando en el feed."""
    sql = _runtime_clause_sql()
    assert "IS NULL" in sql, "runtime NULL debe pasar (la columna es nullable)"
    assert "<= 0" in sql, (
        "runtime = 0 es el centinela de «desconocido» en este catálogo (505 filas, "
        "21 por encima del gate) — sin esta rama desaparecen del feed en silencio"
    )


def test_el_umbral_de_corto_esta_en_la_clausula():
    assert f"> {SHORT_FILM_MAX_RUNTIME}" in _runtime_clause_sql()


def test_include_shorts_quita_el_filtro():
    assert _sql(movie_quality_gate(include_shorts=True)) == _sql(_MOVIE_QUALITY_GATE)
    assert len(movie_quality_gate(True)) == len(movie_quality_gate(False)) - 1


def test_el_gate_base_sigue_intacto():
    """Los invariantes de seguridad no se pierden al añadir la rama de cortos."""
    for variant in (movie_quality_gate(), movie_quality_gate(include_shorts=True)):
        rendered = _sql(variant)
        assert "is_adult" in rendered
        assert "is_excluded" in rendered
        assert "vectorbox_score" in rendered


def test_el_gate_no_se_muta_entre_llamadas():
    """`_MOVIE_QUALITY_GATE + [...]` crea una lista nueva; un `.append()` por
    descuido dejaría el filtro pegado para siempre y también para include_shorts."""
    antes = len(_MOVIE_QUALITY_GATE)
    movie_quality_gate()
    movie_quality_gate(include_shorts=True)
    movie_quality_gate()
    assert len(_MOVIE_QUALITY_GATE) == antes


def test_umbral_es_la_definicion_de_la_academia():
    assert SHORT_FILM_MAX_RUNTIME == 40


@pytest.mark.integration
@pytest.mark.asyncio
async def test_contra_la_bd_real():
    """Cuenta filas de verdad: ningún corto pasa, y la duración 0 sí pasa.

    Marcado `integration` porque necesita Postgres — la suite por defecto es
    hermética. Es el único de este fichero que puede detectar que la cláusula
    dejó de hacer lo que dice.
    """
    from sqlalchemy import select, func
    from config import AsyncSessionLocal
    from models.database import Movie

    async with AsyncSessionLocal() as s:
        colados = await s.scalar(
            select(func.count(Movie.id)).where(
                *movie_quality_gate(),
                Movie.runtime > 0,
                Movie.runtime <= SHORT_FILM_MAX_RUNTIME,
            )
        )
        assert colados == 0, f"{colados} cortos siguen pasando el gate"

        desconocidas = await s.scalar(
            select(func.count(Movie.id)).where(*movie_quality_gate(), Movie.runtime == 0)
        )
        total_desconocidas = await s.scalar(
            select(func.count(Movie.id)).where(*_MOVIE_QUALITY_GATE, Movie.runtime == 0)
        )
        assert desconocidas == total_desconocidas, (
            "las de duración desconocida deben pasar igual que con el gate base"
        )
