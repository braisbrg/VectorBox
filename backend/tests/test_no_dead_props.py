"""Props declaradas en el frontend que ningún componente lee — el gemelo de
`test_no_dead_parameters`, que sólo mira Python.

Esta clase costó meses de funcionalidad invisible, dos veces:

  · `right-console.tsx` declaraba `onScopeChange` y **nunca lo llamaba**. La
    instalación estaba entera —`app-shell` guardaba `scope`, `feed-container` lo
    mandaba a `getFeed`— pero sin nadie que lo disparase, el feed quedó clavado en
    «global». El filtro de watchlist existía en el backend todo ese tiempo.
  · `movie-card.tsx` declaraba NUEVE props que el componente ignoraba
    (`overview`, `genres`, `badgeType`, `providers`, `variant`, `imdb_rating`,
    `metacritic_rating`, `overview_es`, `forceVectorBoxScore`) mientras
    `movie-carousel` las calculaba y se las pasaba una por una. Restos de la
    migración de UI: el badge pasó a salir de `contributors` y `variant` se quedó
    con un solo render.

En React una prop sin usar **no tiene efecto secundario**, al revés que en Python,
donde `Depends(...)` autoriza al llamante y por eso `test_no_dead_parameters` tuvo
que aprender a perdonar 4 de sus 12 hallazgos. Aquí no hay excepción de ese tipo:
si nadie la lee, no hace nada.

## Falsos positivos que sí hay, y cómo se evitan

  · **Spread o acceso dinámico** (`{...props}`, `props.algo`): el componente puede
    usar la prop sin nombrarla. Esos ficheros se saltan enteros.
  · **Re-exportar el tipo**: una interfaz puede describir el contrato de otro
    componente. Por eso se exige que el nombre no aparezca en NINGUNA otra parte
    del fichero, ni en la firma ni en el cuerpo — no basta con que no se
    desestructure.

Vive en `backend/tests/` porque es donde corre el suite; sólo lee ficheros.
"""
import re
from pathlib import Path

# En el contenedor `frontend/` va montado en la raiz (solo lectura, ver
# docker-compose.yml); en el host esta al lado de `backend/`. Se prueban los dos
# para que el test corra igual dentro y fuera.
_CANDIDATAS = (Path("/frontend"), Path(__file__).resolve().parent.parent.parent / "frontend")
RAIZ = next((p for p in _CANDIDATAS if p.is_dir()), _CANDIDATAS[-1])
EXCLUIDOS = {"node_modules", ".next", "dist", "build"}
INTERFAZ = re.compile(r"interface\s+(\w*Props)\s*\{(.*?)\n\}", re.S)
CAMPO = re.compile(r"^\s*(\w+)\??\s*:", re.M)


def _componentes():
    """`os.walk` podando, no `rglob`: recorrer `node_modules` para descartarlo
    despues costaba 77 segundos de suite."""
    import os
    for base, dirs, ficheros in os.walk(RAIZ):
        dirs[:] = [d for d in dirs if d not in EXCLUIDOS]
        for f in ficheros:
            if f.endswith(".tsx"):
                yield Path(base) / f


def test_hay_algo_que_analizar():
    """Si el barrido no encuentra interfaces, el test de abajo pasa por vacío."""
    total = sum(len(INTERFAZ.findall(p.read_text(encoding="utf-8"))) for p in _componentes())
    assert total > 20, f"solo {total} interfaces *Props encontradas — ¿cambió la ruta?"


def test_ninguna_prop_declarada_se_queda_sin_leer():
    muertas = []
    for fichero in _componentes():
        src = fichero.read_text(encoding="utf-8")
        # Spread o acceso dinámico: no se puede saber estáticamente.
        if "...props" in src or re.search(r"\bprops\.\w", src):
            continue
        for m in INTERFAZ.finditer(src):
            resto = src[: m.start()] + src[m.end():]
            for c in CAMPO.finditer(m.group(2)):
                nombre = c.group(1)
                if not re.search(r"\b" + re.escape(nombre) + r"\b", resto):
                    muertas.append(
                        f"{fichero.relative_to(RAIZ).as_posix()}  {m.group(1)}.{nombre}"
                    )

    assert not muertas, (
        "Declaradas y nunca leídas: el padre las calcula y las pasa, y el componente "
        "las tira. Es como el feed se quedó clavado en «global» durante meses.\n    "
        + "\n    ".join(muertas)
        + "\n\nO se usan, o se borran de la interfaz Y de quien las pasa."
    )
