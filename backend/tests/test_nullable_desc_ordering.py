"""`ORDER BY <nullable> DESC` sin `nullslast()` — la clase que ya mordió CINCO veces.

Postgres ordena los NULL **primero** en DESC. No falla, no avisa: devuelve el orden
contrario al que pediste. Instancias reales:

  · 2026-08-11, cuatro sitios — "lo último que has visto" encabezado por The Dark
    Knight, Inception y Fight Club, que son justo las que NO tenían fecha.
  · 2026-08-17, un quinto — "Top Rated in Your Watchlist" abría con las 17 películas
    SIN puntuar de u212, o sea que la fila llamada "mejor valoradas" mostraba
    exactamente aquellas de las que no sabíamos nada.

CLAUDE.md lo lleva escrito como invariante desde la primera barrida y aun así volvió.
Una regla que sólo está escrita se vuelve a violar; una que se ejecuta, falla el suite.

## Por qué mira la SENTENCIA y no la línea

La primera versión de este test miraba sólo la línea del `desc()` y cazó 21 sitios, de
los que la mayoría eran **falsos positivos**: `.where(Movie.vectorbox_score > 70)` ya
excluye los NULL, así que ahí `nullslast()` sobra. Un check que grita en falso se acaba
borrando, y entonces la regla queda sin enforcement — que es justo como llegamos aquí.

Así que se localiza la sentencia completa con `ast` y se da por protegida si dentro hay
cualquier cosa que impida que esa columna sea NULL:

  · `nullslast()` / `nulls_last(...)`      — lo explícito
  · `.isnot(None)` / `.is_not(None)`       — el filtro directo
  · una comparación (`>= 60`, `> 70`, ...) — en SQL NULL nunca satisface una comparación

Lo que NO puede ver: un helper que mete el filtro (`movie_quality_gate()` incluye
`Movie.vectorbox_score.isnot(None)`). Por eso, ante la duda, marca — y si el sitio está
protegido por un helper, se le añade el `nullslast()` igualmente: es una línea, y deja el
invariante visible donde se lee la query en vez de a dos saltos de distancia.
"""
import ast
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
EXCLUDED_DIRS = {"tests", ".venv", "__pycache__", "alembic", "migrations", "scripts"}

# `desc(Modelo.columna)` y `Modelo.columna.desc()`
PATRON = re.compile(r"desc\(\s*([A-Z]\w+)\.(\w+)\s*\)|([A-Z]\w+)\.(\w+)\.desc\(\)")


def _columnas_nullable() -> dict[str, set[str]]:
    """{'Movie': {'vectorbox_score', ...}} leyendo los modelos declarativos.

    En SQLAlchemy una `Column` es nullable salvo `nullable=False` o `primary_key`.
    `server_default` NO implica NOT NULL, así que no cuenta.
    """
    arbol = ast.parse((BACKEND / "models" / "database.py").read_text(encoding="utf-8"))
    salida: dict[str, set[str]] = {}
    for clase in [n for n in arbol.body if isinstance(n, ast.ClassDef)]:
        nullables = set()
        for stmt in clase.body:
            if not isinstance(stmt, ast.Assign) or not isinstance(stmt.value, ast.Call):
                continue
            fn = stmt.value.func
            if getattr(fn, "id", None) != "Column" and getattr(fn, "attr", None) != "Column":
                continue
            kw = {k.arg: k.value for k in stmt.value.keywords}
            es_pk = isinstance(kw.get("primary_key"), ast.Constant) and kw["primary_key"].value
            no_nula = isinstance(kw.get("nullable"), ast.Constant) and kw["nullable"].value is False
            if es_pk or no_nula:
                continue
            for t in stmt.targets:
                if isinstance(t, ast.Name):
                    nullables.add(t.id)
        if nullables:
            salida[clase.name] = nullables
    return salida


_ESQUEMA = _columnas_nullable()

# Sólo las columnas nullable **con intención**, y medido contra la base real el
# 2026-08-18 — no todas las que el esquema permite:
#
#   movies.vectorbox_score      1.271 NULL de 21.374   (sin puntuar todavía)
#   user_ratings.watched_date   1.735 NULL de  4.119   (NULL = no se sabe, por diseño)
#   user_ratings.rating         1.773 NULL de  4.119   (en la lista, sin puntuar)
#   movies.runtime                461 NULL              (NULL = no se sabe, no "corto")
#
# El resto de columnas nullable del esquema (popularity, vote_count, created_at,
# processed_at, movie_count) tienen `default=` y **cero NULL** en la práctica, así que
# marcarlas era gritar en falso: la primera versión de este test sacó 21 sitios y sólo
# tres eran de verdad. Si aparece una columna nueva que sí puede ser NULL, se añade
# aquí con su recuento — el número es lo que justifica la entrada.
NULLABLES = {
    "Movie": {"vectorbox_score", "runtime"},
    "UserRating": {"watched_date", "rating"},
}


def _sentencia_de(arbol, linea: int) -> tuple[int, int] | None:
    """El rango de la sentencia MÁS PEQUEÑA que contiene esa línea."""
    mejor = None
    for n in ast.walk(arbol):
        if isinstance(n, ast.stmt) and n.lineno <= linea <= (n.end_lineno or n.lineno):
            span = (n.end_lineno or n.lineno) - n.lineno
            if mejor is None or span < mejor[0]:
                mejor = (span, n.lineno, n.end_lineno or n.lineno)
    return (mejor[1], mejor[2]) if mejor else None


def _protegida(texto: str, columna: str) -> bool:
    if "nullslast" in texto or "nulls_last" in texto:
        return True
    if re.search(rf"\.{columna}\s*\.\s*is_?not\s*\(\s*None\s*\)", texto):
        return True
    # Cualquier comparación excluye NULL en SQL.
    return bool(re.search(rf"\.{columna}\s*(>=|<=|>|<|==|!=)", texto))


def _ficheros():
    for p in BACKEND.rglob("*.py"):
        if not any(part in EXCLUDED_DIRS for part in p.parts):
            yield p


def test_los_modelos_se_leen():
    """Si esto falla, el test de abajo pasa por vacío y no protege nada."""
    assert "vectorbox_score" in NULLABLES.get("Movie", set())
    assert "watched_date" in NULLABLES.get("UserRating", set())


def test_ningun_desc_sobre_columna_nullable_queda_sin_proteger():
    infractores = []
    for fichero in _ficheros():
        fuente = fichero.read_text(encoding="utf-8")
        try:
            arbol = ast.parse(fuente)
        except SyntaxError:
            continue
        lineas = fuente.splitlines()
        for n, linea in enumerate(lineas, 1):
            for m in PATRON.finditer(linea):
                modelo, col = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
                if col not in NULLABLES.get(modelo, set()):
                    continue
                rango = _sentencia_de(arbol, n)
                texto = "\n".join(lineas[rango[0] - 1:rango[1]]) if rango else linea
                if not _protegida(texto, col):
                    infractores.append(
                        f"{fichero.relative_to(BACKEND)}:{n}  {modelo}.{col}"
                    )

    assert not infractores, (
        "Postgres pone los NULL PRIMERO en DESC: esto devuelve el orden contrario sin "
        "fallar ni avisar.\n    " + "\n    ".join(infractores)
        + "\n\nArréglalo con nulls_last(Modelo.col.desc()) o .desc().nullslast()."
    )


def test_la_rotacion_de_refresh_tiene_orden():
    """`LIMIT` sin `ORDER BY` no es una cola, es una muestra arbitraria del heap.

    Hermano del caso de arriba: allí el `desc()` sobre nullable devolvía el orden
    contrario, aquí la AUSENCIA de orden hace que la misma centena vuelva run tras run
    y el resto no se refresque nunca. Medido el 2026-08-19: 38 filas de
    `movie_availability` en ES ancladas 5 meses mientras el resto del país iba al día.

    Y necesita `nulls_first` porque en ASC Postgres manda los NULL al FINAL, que aquí
    son las 422 películas **jamás refrescadas** — las más urgentes, las últimas.
    """
    texto = (Path(__file__).resolve().parents[1] / "scripts" / "refresh_metadata.py").read_text(
        encoding="utf-8"
    )
    assert "order_by(Movie.last_metadata_refresh.asc().nulls_first())" in texto, (
        "get_movies_to_refresh volvió a hacer LIMIT sin ORDER BY (o perdió el "
        "nulls_first): la rotación deja de rotar y unas filas no se refrescan jamás"
    )
