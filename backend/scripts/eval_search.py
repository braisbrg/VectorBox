"""Mide la calidad de la búsqueda contra el golden set — Fase 0, 2026-08-03.

La razón de existir: toda la afinación de esta búsqueda se hizo con constantes
elegidas sobre paneles de diez o doce casos mirados a ojo. Eso encontró bugs
reales, pero no distingue una mejora de un cambio de sabor, y dos veces dio por
buena una conclusión que la cola de los resultados desmentía. Sin un número
reproducible, cada fase siguiente sería otra vez fe.

    docker compose exec backend python scripts/eval_search.py
    docker compose exec backend python scripts/eval_search.py --lexical on
    docker compose exec backend python scripts/eval_search.py --json

Determinista: usa `forced_intent`, así que no llama a Groq y se puede repetir
tantas veces como haga falta con el mismo resultado. La verificación de la
Fase 0 es precisamente esa — dos pasadas, los mismos números.

Métricas:
  nDCG@10   calidad del ORDEN, con relevancia graduada (0/1/2)
  Recall@20 cuántas de las relevantes conocidas llega a devolver
  MRR@5     familia de entidad: en qué puesto sale la película correcta

DOS COSAS QUE ESTE BANCO NO PUEDE VER, y las dos han mentido ya:

1. **El catálogo tiene que ser el mismo, y "el mismo" NO es el de Postgres.** Es
   determinista en un instante dado, no entre instantes: un sync completo ingestó
   781 películas en una tarde (2026-08-10) y Recall@20 fue bajando 0.738 → 0.734 →
   0.729 sin que nadie tocara la búsqueda. Lo que importa es la población que pasa
   la puerta del enriquecimiento, porque `search_similar` filtra
   `has_enriched_embedding=True`: con Postgres ya congelado en 21.168, el contador
   de enriquecidas en Qdrant seguía subiendo ~15/minuto mientras el backlog drenaba.
   Comprueba las DOS:
       select count(*) from movies where created_at::date = current_date
       count(has_enriched_embedding=True) en Qdrant, dos veces separadas
   (En una ventana de 300 s con +22 enriquecidas el banco no se movió, así que la
   deriva es lenta: para verla hacen falta decenas de minutos, no minutos.)

   **Y compara SIEMPRE por consulta, con `--json`.** Guardar sólo la MEDIA de cada
   pasada deja un −0.005 imposible de atribuir: con la tabla por consulta se ve al
   instante que se movió una sola fila (fue `atracos 70s`, Recall 0.696 → 0.652, que
   son los −0.005 enteros repartidos entre doce). Es la regla de comparar PAREADO
   de CLAUDE.md aplicada a este banco.

2. **Mide ORDEN, no MAGNITUD.** Aprobó con un 0.874 idéntico un cambio de escala
   que hundió las filas del showcase de ~85 de media a 60–65. Se creyó que el
   suelo de confianza del showcase las habría rechazado; no podía — leía una
   escala con suelo en 60 contra un mínimo de 55, y se borró el 2026-08-11.
   Cualquier cambio en `utils/scoring.py` necesita ADEMÁS
   `tests/test_similarity_scale.py` y mirar la salida real de `warm_showcase.py`.
"""
import argparse
import asyncio
import json
import logging
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import AsyncSessionLocal
from routers.search import SearchRequest, _pick_reference_movie, _run_natural_search
from services import lexical_channel
from services.embedding_service import EmbeddingService
from services.nlp_search import MovieSearchIntent
from services.qdrant_service import QdrantService
from services.tmdb_client import TMDBClient
from tests.fixtures.search_relevance import ENTITY_QUERIES, GRADES, QUERIES

logging.getLogger().setLevel(logging.ERROR)


def dcg(grades: list[int]) -> float:
    return sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(grades))


def ndcg_at(grades_in_order: list[int], ideal: list[int], k: int) -> float:
    best = dcg(sorted(ideal, reverse=True)[:k])
    return dcg(grades_in_order[:k]) / best if best else 0.0


async def eval_descriptive(tmdb, qdrant, emb, verbose):
    rows = []
    for label, kwargs in QUERIES.items():
        graded = GRADES[label]
        intent = MovieSearchIntent(reasoning="eval", **kwargs)
        async with AsyncSessionLocal() as db:
            resp = await _run_natural_search(
                SearchRequest(query=label, forced_intent=intent),
                None, db, tmdb, qdrant, emb,
            )
        # La red de seguridad devuelve cine de FUERA de los filtros a propósito y
        # marcado; es otra función de producto y tiene sus propios tests. Medirla
        # aquí penalizaría un comportamiento que decidimos querer.
        results = [m for m in (resp.results or []) if not m.get("outside_filters")]
        ids = [m["movie_id"] for m in results]
        got = [graded.get(i, 0) for i in ids]
        ideal = list(graded.values())
        relevant = {i for i, g in graded.items() if g >= 1}
        recall = len(relevant & set(ids[:20])) / len(relevant) if relevant else 0.0
        rows.append({
            "query": label, "n": len(ids),
            "ndcg@10": ndcg_at(got, ideal, 10), "recall@20": recall,
            "irrelevantes@10": sum(1 for g in got[:10] if g == 0),
        })
        if verbose:
            print(f"\n  {label}")
            for m, g in zip(results[:10], got[:10]):
                print(f"     [{g}] {m['title'][:44]} ({m.get('year')})")
    return rows


async def eval_entity():
    rows = []
    for needle, expected, why in ENTITY_QUERIES:
        async with AsyncSessionLocal() as db:
            # Una sola pasada por el mismo camino que usa la búsqueda por título.
            movie = await _pick_reference_movie(db, needle, substring=True)
        rank = 1 if (movie and movie.tmdb_id in expected) else 0
        rows.append({
            "query": needle, "rr": float(rank),
            "got": movie.title if movie else None, "why": why,
        })
    return rows


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lexical", choices=["on", "off"],
                    help="fuerza el canal léxico (por defecto, lo que traiga el módulo)")
    ap.add_argument("--verbose", action="store_true", help="lista el top-10 con su nota")
    ap.add_argument("--json", action="store_true", help="salida cruda, para comparar pasadas")
    args = ap.parse_args()

    if args.lexical:
        lexical_channel.ENABLED = args.lexical == "on"

    tmdb, qdrant, emb = TMDBClient(), QdrantService(), EmbeddingService()
    desc = await eval_descriptive(tmdb, qdrant, emb, args.verbose)
    ent = await eval_entity()

    if args.json:
        print(json.dumps({"descriptive": desc, "entity": ent}, indent=1, sort_keys=True))
        return 0

    print(f"\n  canal léxico: {'ON' if lexical_channel.ENABLED else 'OFF'}")
    print(f"\n  {'consulta':<22} {'n':>3} {'nDCG@10':>8} {'Recall@20':>10} {'irrel@10':>9}")
    for r in desc:
        print(f"  {r['query']:<22} {r['n']:>3} {r['ndcg@10']:>8.3f} "
              f"{r['recall@20']:>10.3f} {r['irrelevantes@10']:>9}")
    n = len(desc)
    print(f"  {'':<22} {'':>3} {'-' * 8:>8} {'-' * 10:>10}")
    print(f"  {'MEDIA':<22} {'':>3} {sum(r['ndcg@10'] for r in desc) / n:>8.3f} "
          f"{sum(r['recall@20'] for r in desc) / n:>10.3f} "
          f"{sum(r['irrelevantes@10'] for r in desc):>9}")

    print(f"\n  {'entidad':<24} {'ok':>4}  {'devuelve'}")
    for r in ent:
        print(f"  {r['query']:<24} {'si' if r['rr'] else 'NO':>4}  {r['got']}")
    print(f"\n  MRR@5 entidad: {sum(r['rr'] for r in ent) / len(ent):.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
