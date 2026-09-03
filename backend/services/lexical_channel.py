"""The lexical half of retrieval: what the catalogue CALLS a film, not what it feels like.

The vector describes a film's tone and subject through `cinematic_description`,
which is prose written about the film. That prose uses the vocabulary of its own
era, so a query phrased in today's words can miss a film that is exactly what was
asked for. Two measured on 2026-07-31, both present in the catalogue with the
right keyword sitting unused in the Qdrant payload:

    "zombie outbreak, undead, survival horror, infection"  1940-49
        vector:  The Mummy's Hand, House of Frankenstein, House of Dracula
        missing: I Walked with a Zombie (1943) — keyword `zombie`

    "found footage, handheld camera, first person horror"  1960-69
        missing: A Man Vanishes (1967) — keyword `found footage`

A correction worth keeping: `The War Game` (1966) was the headline example for
that second case and it was wrong. The vector did return it, further down the
row; the claim came from reading a top-six as if it were the whole answer. The
test that would have proved it now asserts its own premise first.

`QdrantService.search_similar` has supported `include_keywords` (a MatchAny over
the payload) since it was written; nothing ever passed it. This module decides
WHICH terms to pass, deterministically — no new LLM field, because a parser that
invents a keyword invents a filter, and every optional field the model can fill
has eventually been filled when it should not have been (see
nlp_search.guard_language_filter).
"""
from __future__ import annotations

import logging
import re
import unicodedata
from typing import Iterable, Optional

from sqlalchemy import text

logger = logging.getLogger(__name__)

# A term is useful only if it SEPARATES films. Measured over the catalogue:
# 23,388 distinct keywords across 20,372 films, of which 18,261 appear in five
# films or fewer and just nine in more than five hundred. So the cut is cheap and
# throws away almost nothing: at 1% it drops roughly two hundred terms.
#
# It also removes the metadata masquerading as theme, which is what would have
# poisoned this channel first — `based on novel or book` (8.9% of the catalogue),
# `woman director` (7.1%), `sequel` (5.2%), `duringcreditsstinger` (2.8%). None
# of those says anything about what a film is, and all of them match thousands.
KEYWORD_MAX_DOC_FREQ = 0.01

# Longest phrase to look for. "found footage" and "living dead" are the reason
# this is not 1; nothing useful in the vocabulary runs longer than three words.
MAX_PHRASE_WORDS = 3

# Enough to describe a theme, few enough that MatchAny stays a narrow filter
# rather than a second way of asking for everything.
MAX_TERMS = 6

# ON, sobre la evidencia del golden set — 2026-08-04.
#
# Estuvo apagado desde el 31/07 por una contaminación de cola vista A OJO: para
# "giallo, stylish murder mystery, lurid, baroque violence" colaba Star Wars III
# y Chronicle of Anna Magdalena Bach en los puestos 11-20. El panel de precisión
# de entonces medía sólo el top-10 y no lo veía, así que apagarlo fue lo prudente.
#
# El golden set (Fase 0) lo mide de verdad, cola incluida vía Recall@20:
#
#     sólo denso                 0.826 / 0.689   irrelevantes@10: 10
#     con canal léxico           0.873 / 0.738   irrelevantes@10: 12
#
# +0.047 de nDCG y +0.049 de Recall, a cambio de dos irrelevantes más repartidas
# entre doce consultas. Y la contaminación concreta desapareció: ambas películas
# están fuera de la fila de giallo, porque el acantilado ahora se aplica POR
# CANAL — cada uno propone lo cercano a su propio mejor — en vez de eximir al
# léxico entero, que es lo que las dejaba pasar.
ENABLED = True

_vocabulary: Optional[frozenset[str]] = None


def _fold(s: str) -> str:
    """Lowercase, strip accents, collapse anything else to single spaces."""
    stripped = "".join(
        c for c in unicodedata.normalize("NFKD", s or "")
        if not unicodedata.combining(c)
    )
    return re.sub(r"[^a-z0-9]+", " ", stripped.lower()).strip()


async def load_vocabulary(db, force: bool = False) -> frozenset[str]:
    """Catalogue keywords that are specific enough to retrieve on.

    Cached per process after the first call: 23k short strings is about two
    megabytes, and the set only changes when the catalogue is re-seeded.
    """
    global _vocabulary
    if _vocabulary is not None and not force:
        return _vocabulary
    # The cutoff is computed here rather than inside the SQL: as a bound float
    # parameter asyncpg typed `COUNT(*) * :max_df` in a way that matched nothing
    # and returned an EMPTY vocabulary with no error — the same query with the
    # value inlined returned 23,324. A silent zero would have disabled this whole
    # channel while every test still passed. An integer bound is unambiguous.
    total = (await db.execute(text(
        "SELECT COUNT(*) FROM movies WHERE is_excluded IS FALSE"
    ))).scalar() or 0
    cutoff = max(1, int(total * KEYWORD_MAX_DOC_FREQ))
    rows = await db.execute(text(
        """
        SELECT k FROM (
            SELECT k, COUNT(*) AS df
            FROM movies, unnest(keywords) k
            WHERE is_excluded IS FALSE
            GROUP BY k
        ) x
        WHERE df <= :cutoff
        """
    ), {"cutoff": cutoff})
    _vocabulary = frozenset(_fold(r[0]) for r in rows if r[0])
    if not _vocabulary:
        logger.error("Lexical vocabulary is EMPTY — the keyword channel is off")
    logger.info("Lexical vocabulary loaded: %d keywords", len(_vocabulary))
    return _vocabulary


def terms_in(query: str, vocabulary: Iterable[str]) -> list[str]:
    """Catalogue keywords the query actually contains, longest phrase first.

    Pure and deterministic: a term is returned only when it already exists as a
    keyword on some film, so this can never invent a filter. Longest-first means
    "found footage" is taken as one term instead of contributing a useless
    "footage" — and once taken, its words are consumed so it cannot double-count.
    """
    vocab = vocabulary if isinstance(vocabulary, (set, frozenset)) else frozenset(vocabulary)
    words = _fold(query).split()
    found: list[str] = []
    used = [False] * len(words)
    for size in range(MAX_PHRASE_WORDS, 0, -1):
        for i in range(len(words) - size + 1):
            if any(used[i:i + size]):
                continue
            phrase = " ".join(words[i:i + size])
            if phrase in vocab and phrase not in found:
                found.append(phrase)
                for j in range(i, i + size):
                    used[j] = True
    return found[:MAX_TERMS]
