"""Backfill the payload fields Qdrant needs to filter on — 2026-07-29.

Country, spoken language, certification, Oscar wins and the adult flag were
enforced in Postgres AFTER the vector search returned, because they were not in
the Qdrant payload. That is post-filter starvation: they could only ever subtract
from the twenty nearest neighbours of the query vector, and for a constraint
orthogonal to the theme almost nothing survives. Measured, "thrillers coreanos"
kept ONE film of the 219 Korean ones the catalogue holds.

`_qdrant_payload` writes them now, so every point touched by a re-embed or an
enrichment carries them. This script is for everything written before that —
`set_payload` only, so it never touches a vector and can run on a live system.

    docker compose exec backend python scripts/sync_qdrant_payload.py --dry-run
    docker compose exec backend python scripts/sync_qdrant_payload.py

Run it ONCE after deploying the payload change. Re-running is harmless: it writes
the same values. Verify with scripts/verify_search_branches.py, which needs no
LLM budget.

Related: scripts/recalc_vbs_from_db.py --sync-payload-only does the same job for
vectorbox_score + has_enriched_embedding. Kept separate because that one exists
to propagate a scoring formula change and is run on a different cadence.
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from config import AsyncSessionLocal
from models.database import Movie
from services.qdrant_service import QdrantService

BATCH = 500


async def rellenar_claves_ausentes(qd: QdrantService, dry_run: bool, limit: int | None) -> int:
    """`--fill-missing`: escribe SÓLO las claves que le faltan a un punto.

    Motivo, medido el 2026-08-18: 931 de 21.374 puntos (4,4%) se quedaron en un
    esquema de payload anterior y **no tienen diez claves** — `vote_average`,
    `original_language`, `overview`, `countries`, `spoken_languages`, `oscar_wins`,
    `mpaa_rating`, `is_adult`, `mood_gravedad` y `mood_humanidad` — más `tmdb_id`,
    `directors` y `cast` en 925 de ellos. Llevan `rating`, el nombre viejo de
    `vote_average`. No son títulos menores: Forrest Gump, Finding Nemo, Apocalypse
    Now, Kill Bill.

    Consecuencia: **todo filtro que viva en Qdrant los descarta**, sin un error en
    ningún log. La puerta de calidad del Magic Box los tiraba enteros, y los cinco
    cuadrantes de ánimo no pueden verlos. `tests/test_qdrant_payload_writers.py`
    puso la guardia el 2026-07-30 para que ningún escritor NUEVO vuelva a tirar
    claves; lo que nadie hizo fue reparar los puntos ya escritos.

    **Rellena, nunca pisa.** Es la misma regla que `payload_for` explica arriba: una
    fila de Postgres puede ser más vieja que el enriquecimiento que hay en el punto,
    así que sobrescribir un `overview` existente sería un retroceso. Escribir uno que
    NO existe no puede serlo. Por eso se lee el payload antes y se escribe la
    diferencia — y por eso re-ejecutarlo no hace nada la segunda vez.

    `mood_*` SÍ se rellena desde 2026-08-19: lo calcula `compute_mood_axes.py`
    proyectando el vector, pero aterriza en una columna (`Movie.mood_gravedad`),
    así que va en `qdrant_payload()` como cualquier otra y este paso lo repara.
    Antes de entrar en el constructor canónico, cada re-upsert lo borraba del
    punto — 7 puntos tenían el valor en Postgres y no en el payload.
    """
    from models.external_schemas import qdrant_payload

    puntos: dict[int, set] = {}
    off = None
    while True:
        pts, off = await qd.client.scroll(
            collection_name=qd.COLLECTION_NAME, limit=2000, offset=off,
            with_payload=True, with_vectors=False,
        )
        for p in pts:
            puntos[p.id] = set((p.payload or {}).keys())
        if off is None:
            break

    async with AsyncSessionLocal() as db:
        movies = (await db.execute(
            select(Movie).where(Movie.tmdb_id.in_(list(puntos))).order_by(Movie.id)
        )).scalars().all()

    from collections import Counter
    faltantes = Counter()
    # Segundo contador, y la razón de que exista: `hueco` sólo ve lo que PG puede
    # rellenar, así que este script contestaba «cuántos puedo REPARAR» mientras se
    # leía como «cuántos están ROTOS». El 2026-09-01 dijo «0 incompletos de 21.405»
    # con 819 puntos a los que les faltaban DOCE claves, y una auditoría lo dio por
    # verde. Un punto sin `mood_gravedad` es invisible a los cinco cuadrantes tenga
    # PG el valor o no; que no podamos arreglarlo desde aquí no lo hace estar sano.
    ausentes = Counter()
    sin_reparacion = Counter()
    puntos_con_ausencias = 0
    escritos = 0
    for m in movies:
        tiene = puntos.get(m.tmdb_id) or set()
        completo = qdrant_payload(m)
        ausente = {k: v for k, v in completo.items() if k not in tiene}
        if ausente:
            puntos_con_ausencias += 1
            for k, v in ausente.items():
                ausentes[k] += 1
                if v is None:
                    sin_reparacion[k] += 1
        # Sin los `None`: para Qdrant una clave ausente y una clave a null filtran
        # igual, así que escribirlos no arregla nada y multiplica el trabajo — el
        # primer ensayo daba 6.193 puntos "incompletos" de los que 5.878 lo eran
        # sólo por un `mpaa_rating` que en Postgres tampoco existe.
        hueco = {k: v for k, v in completo.items() if k not in tiene and v is not None}
        if not hueco:
            continue
        for k in hueco:
            faltantes[k] += 1
        escritos += 1
        if not dry_run:
            # `wait=True` y no `False`: medido el 2026-09-01, una pasada con
            # fire-and-forget dejó **155 de 821** puntos sin escribir y aun así
            # imprimió «Rellenados 821». Un reparador que no confirma sus
            # escrituras miente en la dirección peor — la de dar por arreglado.
            # La segunda pasada los cogió, pero nadie iba a mirar dos veces.
            await qd.client.set_payload(
                collection_name=qd.COLLECTION_NAME, payload=hueco,
                points=[m.tmdb_id], wait=True,
            )
        if limit and escritos >= limit:
            break

    print(f"\n{'Se rellenarían' if dry_run else 'Rellenados'} {escritos} puntos "
          f"de {len(puntos)} — REPARABLES (Postgres tiene el valor)")
    for k, n in faltantes.most_common():
        print(f"   {k:24s} faltaba en {n:>5}")

    # Las dos cifras SIEMPRE, incluso cuando la primera es 0: leer sólo aquélla
    # es exactamente el fallo que este bloque existe para no repetir.
    print(f"\n{puntos_con_ausencias} puntos de {len(puntos)} con ALGUNA clave "
          f"ausente (reparable o no)")
    for k, n in ausentes.most_common():
        marca = f"  <- {sin_reparacion[k]} sin valor en PG" if sin_reparacion.get(k) else ""
        print(f"   {k:24s} ausente en {n:>5}{marca}")
    if sin_reparacion:
        print("\n   AVISO: las marcadas NO las arregla este script, falta el dato en PG.")
        print("   mood_* lo calcula scripts/compute_mood_axes.py; el resto refresh_metadata.py.")
    return 0


def payload_for(m: Movie) -> dict:
    """The five fields this script owns. Deliberately NOT the whole payload —
    overwriting title/overview/vectors here would let a stale DB row clobber a
    fresher enrichment."""
    return {
        "countries": m.omdb_countries or [],
        "spoken_languages": m.omdb_languages or [],
        "mpaa_rating": m.mpaa_rating,
        "oscar_wins": m.oscar_wins or 0,
        "is_adult": bool(m.is_adult),
    }


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="cuenta y muestra, sin escribir")
    ap.add_argument("--limit", type=int, help="procesa solo N películas (pruebas)")
    ap.add_argument("--fill-missing", action="store_true",
                    help="rellena TODA clave canónica ausente, sin pisar las que ya están")
    args = ap.parse_args()

    qd = QdrantService()
    await qd.init_payload_indexes()

    if args.fill_missing:
        return await rellenar_claves_ausentes(qd, args.dry_run, args.limit)

    async with AsyncSessionLocal() as db:
        q = select(Movie).where(Movie.tmdb_id.is_not(None)).order_by(Movie.id)
        if args.limit:
            q = q.limit(args.limit)
        movies = (await db.execute(q)).scalars().all()

    print(f"{len(movies)} películas en Postgres{' [DRY RUN]' if args.dry_run else ''}\n")

    stats = {"countries": 0, "spoken_languages": 0, "mpaa_rating": 0, "oscar_wins": 0}
    written = 0
    for i in range(0, len(movies), BATCH):
        chunk = movies[i:i + BATCH]
        for m in chunk:
            p = payload_for(m)
            if p["countries"]:
                stats["countries"] += 1
            if p["spoken_languages"]:
                stats["spoken_languages"] += 1
            if p["mpaa_rating"]:
                stats["mpaa_rating"] += 1
            if p["oscar_wins"]:
                stats["oscar_wins"] += 1
            if not args.dry_run:
                # One call per point: set_payload takes a single payload dict, and
                # these values differ per film. 20k small calls against a local
                # Qdrant is a couple of minutes, and this runs once.
                await qd.client.set_payload(
                    collection_name=qd.COLLECTION_NAME,
                    payload=p,
                    points=[m.tmdb_id],
                    wait=False,
                )
            written += 1
        print(f"  {min(i + BATCH, len(movies)):>6}/{len(movies)}")

    print(f"\n{'Se escribirían' if args.dry_run else 'Escritos'} {written} puntos")
    for k, n in stats.items():
        print(f"   {k:18s} con valor: {n:>6} ({100 * n / len(movies):.1f}%)")
    if not args.dry_run:
        print("\nVerifica con: python scripts/verify_search_branches.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
