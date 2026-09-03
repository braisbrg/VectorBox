"""BM25 sparse vectors — el canal léxico, esta vez bien planteado.

Sustituye al `include_keywords` que había antes. Aquel hacía un OR binario sobre
keywords del catálogo con un corte de frecuencia al 1%, que era un **IDF
binario**: `giallo` (6 películas) y `baroque` (3) entraban con el mismo peso, y
por eso `baroque` metía a Bach en una consulta de giallo. BM25 pesa cada término
por su rareza en vez de admitirlo o no, que es exactamente la pieza que faltaba.

El IDF NO se calcula aquí: la colección declara `Modifier.IDF`, así que Qdrant lo
computa en el servidor sobre el corpus real y se mantiene solo cuando el catálogo
crece. Aquí sólo se produce la saturación por frecuencia de término y la
normalización por longitud del documento — la otra mitad de la fórmula.

Sin `fastembed` a propósito. Trae onnxruntime entero para, en este caso,
tokenizar; y la escala aquí son 20k documentos de texto corto. Si se demuestra
que el stemming es lo que separa el resultado de lo que buscamos, se añade
entonces y con la medida delante.
"""
from __future__ import annotations

import math
import re
import unicodedata

# Parámetros canónicos de BM25. k1 controla cuánto satura repetir un término, b
# cuánto penaliza que el documento sea largo. Los valores estándar existen porque
# funcionan en casi todo corpus; tocarlos sin un golden set delante es adivinar.
K1 = 1.2
B = 0.75

# Longitud media de documento del catálogo. Se pasa desde fuera al indexar (la
# calcula el script de migración) y queda fijada en el módulo para que consultar
# y indexar usen la misma. Un valor por defecto razonable evita que un cálculo
# olvidado rompa la normalización en silencio.
DEFAULT_AVG_LEN = 95.4

# Palabras que aparecen en tantas descripciones que no separan nada. La lista es
# corta a propósito: el IDF del servidor ya hunde a las comunes, así que esto
# sólo evita gastar dimensiones. Incluye las dos lenguas del producto.
_STOP = frozenset("""
a al algo an and any are as at be been both but by can de del did do does el en
for from had has have he her his how i if in into is it its la las le lo los me
mi my no nor not o of on once only or other our out over own para por que quien
se she should so some su sus such than that the their them then there these they
this those through to too un una uno unos up very was we were what when where
which while who whom why will with y you your
""".split())

_WORD = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Minúsculas, sin acentos, sin puntuación, sin palabras vacías.

    Sin stemming: "robbery" y "robberies" son términos distintos para este
    tokenizador. Es una limitación real y está medida en el golden set antes de
    decidir si merece una dependencia.
    """
    folded = "".join(
        c for c in unicodedata.normalize("NFKD", text or "")
        if not unicodedata.combining(c)
    ).lower()
    return [w for w in _WORD.findall(folded) if len(w) > 1 and w not in _STOP]


def term_index(term: str) -> int:
    """Índice estable para un término.

    Qdrant indexa vectores sparse por entero, así que el término se convierte en
    uno. Se usa un hash de 32 bits con semilla fija en vez de `hash()` de Python,
    que está aleatorizado por proceso y produciría un índice distinto en cada
    arranque — el fallo silencioso perfecto: indexas con unos números y consultas
    con otros.
    """
    h = 2166136261
    for ch in term.encode("utf-8"):
        h = ((h ^ ch) * 16777619) & 0xFFFFFFFF
    return h


def document_vector(text: str, avg_len: float = DEFAULT_AVG_LEN) -> dict[int, float]:
    """Vector sparse de un documento: {índice: peso BM25 sin IDF}."""
    tokens = tokenize(text)
    if not tokens:
        return {}
    counts: dict[str, int] = {}
    for t in tokens:
        counts[t] = counts.get(t, 0) + 1
    norm = K1 * (1.0 - B + B * (len(tokens) / (avg_len or 1.0)))
    return {term_index(t): (tf * (K1 + 1.0)) / (tf + norm) for t, tf in counts.items()}


def query_vector(text: str) -> dict[int, float]:
    """Vector sparse de una consulta: presencia, peso 1.

    El peso real lo pone el IDF del servidor al cruzar con el documento. Repetir
    un término en la consulta no debe contar más — quien pregunta no está
    ponderando, está describiendo.
    """
    return {term_index(t): 1.0 for t in set(tokenize(text))}


def payload_text(payload: dict, *, title_boost: int = 3) -> str:
    """Lo mismo que `movie_text`, pero desde el payload de Qdrant.

    Existe para que CUALQUIER escritura de un punto pueda re-generar su vector
    sparse sin consultar Postgres. Hace falta porque un upsert que sólo trae el
    denso BORRA el sparse del punto — comprobado — así que un re-enriquecido o
    un re-sync de VBS iría desnudando el índice léxico en silencio, que es
    exactamente el fallo que CLAUDE.md documenta para `has_enriched_embedding`.

    Indexa MENOS texto que `movie_text`: el payload no lleva `original_title`,
    `collection_name` ni `cinematic_description`. La diferencia es real y está
    acotada — "james bond" encuentra las Bond por `collection_name`, así que una
    película reescrita pierde esa vía hasta que se vuelva a pasar la migración.
    Igualar ambas fuentes exige añadir campos al payload y tocar los cuatro
    escritores; es trabajo aparte, no un descuido.
    """
    parts: list[str] = []
    for _ in range(title_boost):
        parts += [payload.get("title") or "", payload.get("title_es") or ""]
    for key in ("directors", "cast", "keywords", "genres"):
        value = payload.get(key) or []
        parts.append(" ".join(value[:8] if key == "cast" else value))
    parts.append(payload.get("overview") or "")
    return " ".join(p for p in parts if p)


def sparse_from_payload(payload: dict):
    """`models.SparseVector` para un payload, o None si no hay texto."""
    from qdrant_client.models import SparseVector

    weights = document_vector(payload_text(payload or {}))
    if not weights:
        return None
    return SparseVector(indices=list(weights.keys()), values=list(weights.values()))


def movie_text(movie, *, title_boost: int = 3) -> str:
    """El texto por el que se puede encontrar una película.

    El título va repetido: BM25 no tiene campos, así que la única forma de que
    pese más que la sinopsis es que aparezca más veces. Tres es un punto de
    partida, no una constante sagrada — el golden set dirá.

    `cinematic_description` es la fuente del vector denso; aquí entra también
    porque el canal léxico busca la palabra exacta que el denso parafrasea.
    """
    parts: list[str] = []
    for _ in range(title_boost):
        parts += [movie.title or "", movie.original_title or "", movie.title_es or ""]
    parts += [
        " ".join(movie.directors or []),
        " ".join((movie.cast or [])[:8]),
        " ".join(movie.keywords or []),
        " ".join(movie.genres or []),
        movie.collection_name or "",
        movie.cinematic_description or movie.overview or "",
    ]
    return " ".join(p for p in parts if p)
