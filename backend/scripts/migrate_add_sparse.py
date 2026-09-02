"""Fase 1 — añade un vector sparse BM25 a la colección de películas.

Qdrant NO permite añadir un vector sparse a una colección existente
(`update_collection` sólo modifica los que ya están: "Not existing vector name
error"). Así que hay que recrearla. Los vectores densos NO se recalculan: se leen
de la colección actual y se copian, de modo que esto no toca el embedding, ni
obliga a re-clusterizar, ni gasta un segundo de CPU en el modelo.

La colección nueva mantiene el vector denso **sin nombre**, que es lo que asume
todo el código de consulta actual. Comprobado antes de escribir esto: denso sin
nombre + sparse con nombre + fusión RRF en servidor conviven sin tocar una sola
llamada existente.

    docker compose exec backend python scripts/migrate_add_sparse.py --dry-run
    docker compose exec backend python scripts/migrate_add_sparse.py
    docker compose exec backend python scripts/migrate_add_sparse.py --swap

`--swap` es el único paso destructivo y va aparte a propósito: construye,
verifica y para. Se mira el resultado, y sólo entonces se cambia el alias.
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qdrant_client import AsyncQdrantClient, models
from sqlalchemy import select

from config import AsyncSessionLocal
from models.database import Movie
from services import bm25

QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant:6333")
SOURCE = "movies"
TARGET = "movies_v2"
BATCH = 256


async def catalogue_text(db) -> tuple[dict[int, str], float]:
    """Texto indexable por tmdb_id, y la longitud media en tokens.

    La media se calcula sobre el corpus real en vez de fijarse a ojo: es el
    divisor de la normalización por longitud de BM25, y ponerla mal sesga a favor
    de los documentos largos o de los cortos sin que se note en ningún error.
    """
    rows = (await db.execute(
        select(Movie).where(Movie.is_excluded.is_(False))
    )).scalars().all()
    texts = {m.tmdb_id: bm25.movie_text(m) for m in rows if m.tmdb_id}
    lengths = [len(bm25.tokenize(t)) for t in texts.values()]
    avg = sum(lengths) / len(lengths) if lengths else bm25.DEFAULT_AVG_LEN
    return texts, avg


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="mide y no escribe nada")
    ap.add_argument("--swap", action="store_true",
                    help="paso destructivo: borra la original y apunta el alias a la nueva")
    args = ap.parse_args()

    client = AsyncQdrantClient(url=QDRANT_URL)
    src = await client.get_collection(SOURCE)
    print(f"origen: {SOURCE}  puntos={src.points_count}  denso={src.config.params.vectors}")

    async with AsyncSessionLocal() as db:
        texts, avg_len = await catalogue_text(db)
    print(f"texto indexable para {len(texts)} películas · longitud media {avg_len:.1f} tokens")

    sin_texto = 0
    muestra = list(texts.items())[:2]
    for tid, t in muestra:
        v = bm25.document_vector(t, avg_len)
        print(f"  ejemplo {tid}: {len(bm25.tokenize(t))} tokens -> {len(v)} términos únicos")

    if args.swap:
        info = await client.get_collection(TARGET)
        if info.points_count != src.points_count:
            print(f"! {TARGET} tiene {info.points_count} puntos y {SOURCE} {src.points_count}"
                  f" — no se cambia nada")
            return 1
        await client.delete_collection(SOURCE)
        await client.update_collection_aliases(change_aliases_operations=[
            models.CreateAliasOperation(
                create_alias=models.CreateAlias(collection_name=TARGET, alias_name=SOURCE))
        ])
        print(f"alias {SOURCE} -> {TARGET}. Hecho.")
        return 0

    if args.dry_run:
        print("\n[dry-run] no se ha escrito nada")
        return 0

    # Colección nueva: mismo denso (SIN nombre, como el actual) + sparse con IDF.
    try:
        await client.delete_collection(TARGET)
    except Exception:
        pass
    await client.create_collection(
        collection_name=TARGET,
        vectors_config=models.VectorParams(
            size=src.config.params.vectors.size,
            distance=src.config.params.vectors.distance,
            hnsw_config=models.HnswConfigDiff(m=32, ef_construct=200),
        ),
        sparse_vectors_config={
            "lexical": models.SparseVectorParams(modifier=models.Modifier.IDF),
        },
    )
    # Los índices de payload NO viajan con los puntos, y olvidarlos no rompe nada
    # visible: los filtros siguen dando el mismo resultado, sólo que por escaneo
    # completo. Medido la primera vez que se ejecutó esto sin ellos — una consulta
    # filtrada pasó de 6 ms a 159 ms, y ni el golden set ni la suite lo vieron,
    # porque ambos miden qué sale y no cuánto tarda.
    from services.qdrant_service import QdrantService
    svc = QdrantService()
    original, QdrantService.COLLECTION_NAME = QdrantService.COLLECTION_NAME, TARGET
    try:
        await svc.init_payload_indexes()
    finally:
        QdrantService.COLLECTION_NAME = original
    print(f"creada {TARGET} con sus índices de payload")

    copiados = 0
    offset = None
    while True:
        points, offset = await client.scroll(
            collection_name=SOURCE, limit=BATCH, offset=offset,
            with_payload=True, with_vectors=True,
        )
        if not points:
            break
        batch = []
        for p in points:
            tid = int((p.payload or {}).get("tmdb_id") or p.id)
            text = texts.get(tid)
            vector = {"": p.vector if not isinstance(p.vector, dict) else p.vector.get("")}
            if text:
                sparse = bm25.document_vector(text, avg_len)
                if sparse:
                    vector["lexical"] = models.SparseVector(
                        indices=list(sparse.keys()), values=list(sparse.values()))
            batch.append(models.PointStruct(id=p.id, vector=vector, payload=p.payload))
        await client.upsert(collection_name=TARGET, points=batch, wait=False)
        copiados += len(batch)
        if copiados % (BATCH * 8) == 0:
            print(f"  {copiados}/{src.points_count}")
        if offset is None:
            break

    # Los lotes van con wait=False para no pagar un fsync por cada 256 puntos.
    # Qdrant rechaza un upsert vacío ("Empty update request"), así que la barrera
    # es esperar a que el contador alcance al origen en vez de fingir una
    # escritura. Con techo: si no llega, hay que verlo, no colgarse.
    for _ in range(60):
        dst = await client.get_collection(TARGET)
        if dst.points_count >= copiados:
            break
        await asyncio.sleep(1)
    dst = await client.get_collection(TARGET)
    print(f"\ncopiados {copiados} · {TARGET} tiene {dst.points_count} puntos")
    sin_sparse = 0
    got, _ = await client.scroll(TARGET, limit=200, with_vectors=True)
    for p in got:
        if not isinstance(p.vector, dict) or "lexical" not in p.vector:
            sin_sparse += 1
    print(f"muestra de 200: {sin_sparse} sin vector sparse")
    print(f"\nsiguiente paso, tras revisar: scripts/migrate_add_sparse.py --swap")
    return 0 if dst.points_count == src.points_count else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
