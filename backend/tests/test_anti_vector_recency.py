"""El anti-vector leía la fecha equivocada y cogía las filas equivocadas.

Dos defectos distintos medidos el 2026-08-19, los dos invisibles: nada falla, nada
se loguea, el vector sale y es el vector de otra cosa.

1. `.limit(50)` SIN `ORDER BY` — Postgres devuelve 50 filas arbitrarias y luego el
   decay las tira. u210 tenía 615 negativas (194 de los últimos 3 años) y de las 50
   que llegaban, 49 caían bajo `MIN_EFFECTIVE_WEIGHT`: quedaba UNA, por debajo del
   mínimo de 3, y el usuario con más datos del sistema se quedaba sin anti-vector.
   El decay tiene que descartar lo viejo, no lo que nadie eligió.

2. `ur.watched_date or ur.created_at` — `created_at` es la fecha de import, y como
   nunca es NULL (0 filas en toda la tabla) la rama neutra de abajo era inalcanzable
   y toda fila sin fecha entraba con peso MÁXIMO, por delante de negativas reales y
   fechadas. Es la regla de CLAUDE.md que ya se había colado tres veces.

El primer check mira la SENTENCIA compilada, no el fichero: una cadena en el código
fuente no prueba que la query lleve el ORDER BY (esa trampa ya costó una sesión aquí).
El segundo es estático y barre `backend/` entero, porque la clase reaparece en sitios
nuevos, no en éste.
"""
import ast
import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
# `scripts` DENTRO, al contrario que en test_nullable_desc_ordering: ahí no es
# código servido pero sí son los INSTRUMENTOS, y un instrumento roto es peor que
# una fila torcida. Al escribir este test cazó 5 sitios allí y del revés
# (`created_at or watched_date`), o sea que `watched_date` no se leía nunca:
# debug_movie.py, experiment_feed_sections.py ×2, experiment_signal_a.py ×2.
# Los tres eran benches que BACKLOG nombra para validar cambios de ranking —
# incluido, con guasa, el propio arreglo de recencia.
EXCLUDED_DIRS = {"tests", ".venv", "__pycache__", "alembic", "migrations"}


class _FakeResult:
    def all(self):
        return []          # < MIN_NEGATIVE_FILMS → la función corta ahí


class _CapturingSession:
    """Se queda con la sentencia en vez de ejecutarla."""

    def __init__(self):
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _FakeResult()


@pytest.mark.asyncio
async def test_negativas_se_piden_por_fecha_descendente_nulls_last():
    from utils.anti_vector import compute_anti_vector

    session = _CapturingSession()
    assert await compute_anti_vector(1, session, qdrant=None) is None

    sql = str(session.statements[0].compile(
        dialect=__import__("sqlalchemy.dialects.postgresql", fromlist=["dialect"]).dialect()
    ))
    assert "ORDER BY" in sql, "el LIMIT sin ORDER BY devuelve 50 filas arbitrarias"
    assert re.search(r"ORDER BY\s+\S*watched_date\s+DESC\s+NULLS LAST", sql), (
        f"se espera ORDER BY watched_date DESC NULLS LAST, salió:\n{sql}"
    )


def _ficheros_backend():
    for ruta in BACKEND.rglob("*.py"):
        if EXCLUDED_DIRS & set(ruta.relative_to(BACKEND).parts):
            continue
        yield ruta


def test_created_at_nunca_es_reserva_de_watched_date():
    """`watched_date or created_at` / `watched_date if ... else created_at`.

    Sin fecha la respuesta es NEUTRA (una vida media, 0.5 en clustering), nunca la
    fecha de import: mezclar los dos marcos hunde lo fechado por debajo de lo que no
    lo está. Busca el patrón sobre el AST para no cazar menciones en comentarios.
    """
    culpables = []
    for ruta in _ficheros_backend():
        arbol = ast.parse(ruta.read_text(encoding="utf-8"), filename=str(ruta))
        for nodo in ast.walk(arbol):
            atributos = [n.attr for n in ast.walk(nodo) if isinstance(n, ast.Attribute)]
            if "watched_date" not in atributos or "created_at" not in atributos:
                continue
            if isinstance(nodo, ast.BoolOp) and isinstance(nodo.op, ast.Or):
                culpables.append(f"{ruta.relative_to(BACKEND)}:{nodo.lineno}  (or)")
            elif isinstance(nodo, ast.IfExp):
                culpables.append(f"{ruta.relative_to(BACKEND)}:{nodo.lineno}  (ternario)")

    assert not culpables, (
        "created_at es la fecha de IMPORT, no la de visionado — sin fecha, neutro:\n  "
        + "\n  ".join(culpables)
    )
