"""El filtro de proveedor en origen: que la SQL diga lo que creemos que dice.

Las filas de base de datos (niche/wildcard/random) dejaron de post-filtrar por
proveedor el 2026-08-17 — post-filtrando, `niche` bajaba de 20 a 3 películas y
`random` desaparecía. Ahora el filtro entra en su propia query con un EXISTS
sobre `movie_availability`.

Lo que se rompería en silencio: cambiar la clave del JSONB (`provider_id`),
perder la condición de país (que mezclaría catálogos de otros países) o perder
la correlación con `movies.id` (que haría el EXISTS cierto para TODA película en
cuanto el servicio tuviera una sola film). Ninguna de las tres falla al ejecutar:
devuelven resultados de más, que es justo lo que un filtro no debe hacer.
"""
import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from models.database import Movie
from services.recommendation_engine import apply_rail_filters, available_on, in_watchlist


def _compilar(clause):
    """Sin `literal_binds`: no sabe renderizar JSONB. El texto lleva la estructura
    y los parámetros llevan los valores, así que se comprueban los dos."""
    c = clause.compile(dialect=postgresql.dialect())
    return str(c), c.params


def test_exists_correlaciona_pelicula_pais_y_proveedor():
    sql, params = _compilar(select(Movie.id).where(available_on([8, 119], "ES")))
    assert "EXISTS" in sql
    assert "movie_availability.movie_id = movies.id" in sql      # correlación
    assert "movie_availability.country_code =" in sql            # país
    assert "ES" in params.values()
    assert sql.count("@>") == 2                                  # un @> por servicio
    assert " OR " in sql                                         # cualquiera, no todos
    assert [{"provider_id": 8}] in params.values()
    assert [{"provider_id": 119}] in params.values()


def test_la_disponibilidad_NO_se_acota_por_frescura_y_es_deliberado():
    """Se probó acotar por `last_updated` el 2026-09-01 y se revirtió.

    Toda la disponibilidad de ES es UN volcado de hace ~15-21 días que nadie
    refresca, así que la cota no quitaba lo rancio: lo quitaba todo. Medido sobre
    6 proveedores reales — 7d: **7 películas de 6.038**; 14d: 64; 21d: 6.038.

    Lo que la cota habría evitado eran 49 filas afirmando disponibilidad con datos
    viejos. Perder 6.031 para arreglar 49 no es un arreglo. Y peor: hoy un TTL de
    21 días no quita nada, así que pasaría los tests y vaciaría la fila sola dentro
    de una semana, sin error — la clase de fallo que este repo colecciona.

    Este test fija la decisión: si alguien vuelve a meter la cota, que sea con un
    refresco que la sostenga. Ver BACKLOG 2026-09-01.
    """
    sql, _ = _compilar(select(Movie.id).where(available_on([8, 119], "ES")))
    assert "last_updated" not in sql, (
        "acotar por frescura sin refrescar la tabla vacía el feed: 6.038 -> 7"
    )

def test_sin_proveedores_la_query_no_cambia():
    base = select(Movie.id)
    for filtros in (None, {}, {"provider_ids": []}, {"year_min": 1990}):
        assert "movie_availability" not in _compilar(apply_rail_filters(base, filtros))[0]


def test_el_pais_por_defecto_es_es():
    _, params = _compilar(apply_rail_filters(select(Movie.id), {"provider_ids": [8]}))
    assert "ES" in params.values()


def test_watchlist_exige_en_lista_y_sin_ver():
    """`is_watched=False` no es opcional: al importar un ZIP una película vista que
    siguiera en la lista de Letterboxd conserva las dos marcas, y sin esa condición
    el feed de "mi lista" propondría cosas que el usuario ya vio."""
    sql, params = _compilar(select(Movie.id).where(in_watchlist(212)))
    assert "EXISTS" in sql
    assert "user_ratings.movie_id = movies.id" in sql
    assert "user_ratings.is_watchlist IS true" in sql
    assert "user_ratings.is_watched IS false" in sql
    assert 212 in params.values()


def test_watchlist_y_proveedor_se_acumulan():
    """Los dos activos = intersección, no uno pisando al otro."""
    sql, _ = _compilar(apply_rail_filters(
        select(Movie.id), {"provider_ids": [8], "watchlist_user_id": 212}
    ))
    assert "movie_availability" in sql and "user_ratings" in sql


class _SesionFalsa:
    """Recoge las sentencias sin tocar Postgres."""
    def __init__(self):
        self.stmts = []

    async def execute(self, stmt):
        self.stmts.append(stmt)


async def _guardar(monkeypatch, providers_data):
    """Ejecuta `_guardar_disponibilidad` con una sesión de mentira."""
    from scripts import refresh_metadata as rm

    sesion = _SesionFalsa()
    monkeypatch.setattr(rm, "async_object_session", lambda _m: sesion)

    class _Peli:
        id = 7

    await rm._guardar_disponibilidad(_Peli(), {"providers_data": providers_data})
    return [str(x.compile(dialect=postgresql.dialect())) for x in sesion.stmts]


@pytest.mark.asyncio
async def test_un_pais_que_desaparece_se_vacia(monkeypatch):
    """El bucle de escritura sólo sabía AÑADIR y ACTUALIZAR.

    Recorre los países que TMDB devuelve, así que cuando una película DEJA de estar en
    España TMDB deja de mandar el bloque `ES` y la fila vieja no se toca nunca: sigue
    afirmando el proveedor para siempre. Medido 2026-08-19: 49 filas de ES mintiendo con
    hasta 5 meses, y **47 de ellas visitadas por este mismo refresco después de esa
    fecha** — no era que el refresco no llegara, es que no podía limpiarlas.
    """
    sql = await _guardar(monkeypatch, {"US": {"flatrate": [{"provider_id": 8, "provider_name": "Netflix"}]}})
    updates = [q for q in sql if q.lstrip().upper().startswith("UPDATE")]
    assert updates, "se perdió el vaciado: un país que desaparece se queda mintiendo"
    assert "NOT IN" in updates[0].upper(), "el vaciado debe respetar los países que SÍ vienen"


@pytest.mark.asyncio
async def test_sin_ningun_pais_se_vacia_todo(monkeypatch):
    """`{}` no es una respuesta mala, es "no está en ningún sitio".

    Si la llamada a TMDB hubiera fallado, `refresh_movie` sale con None antes de llegar
    aquí. Tratar el diccionario vacío como fallo dejaba a Tallulah, A Free Man y
    Emotional Architecture 1959 afirmando Netflix/Filmin/Arte para siempre.
    """
    sql = await _guardar(monkeypatch, {})
    updates = [q for q in sql if q.lstrip().upper().startswith("UPDATE")]
    assert updates, "un providers_data vacío volvió a tratarse como fallo"
    assert "NOT IN" not in updates[0].upper(), "sin países que salvar, se vacían todos"
