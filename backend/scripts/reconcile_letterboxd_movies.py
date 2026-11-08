"""Comprueba que cada película es la que dice su URI de Letterboxd — 2026-11-03.

La versión anterior buscaba en TMDB cada película por SU PROPIO título y llamaba
«discrepancia» a que el primer resultado fuera otra. Una fila equivocada ya lleva
el título de la película equivocada, así que se encontraba a sí misma: no podía
ver el error que buscaba. Lo que sí veía eran homónimos (dos «Piggy» de 2018), y
su `--fix` movía las valoraciones al más popular: el mismo error, fabricado. Siete
falsos positivos acabaron en un fichero de exclusiones a mano.

La verdad que tenemos es la URI, y se comprueba sin red:
  - `letterboxd.com/tmdb/N`: la fila buena es la de tmdb_id == N. Seguro.
  - `letterboxd.com/film/<slug>/`: el slug es el título en Letterboxd; si no se
    parece al de la fila se informa (puede ser un título traducido).
  - Una misma URI en dos filas: al menos una sobra; se informa.
  - `boxd.it/...` no dice nada sin red.

`--fix` sólo actúa en el caso seguro: mueve las valoraciones a la fila de
tmdb_id == N (si existe y el usuario no la tiene ya) y devuelve a la fila
equivocada su propia URI. Medido el 2026-11-03: 18 filas y 7 valoraciones de 2
usuarios, todas por el import que tiraba ese N y buscaba por título.

    docker compose run --rm --no-deps backend python scripts/reconcile_letterboxd_movies.py
    docker compose run --rm --no-deps backend python scripts/reconcile_letterboxd_movies.py --fix
"""
import argparse
import asyncio
import collections
import difflib
import os
import re
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from config import AsyncSessionLocal
from models.database import Movie, UserRating
from services.csv_parser import CSVParser

SLUG_RE = re.compile(r"letterboxd\.com/(?:[^/]+/)?film/([^/]+)/?$")
# Por debajo, el slug no es esta película. Medido sobre las 151 películas de
# biblioteca con slug: ninguna correcta baja de aquí.
SLUG_MIN_RATIO = 0.6


def _slugify(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s.replace("&", "and")).strip("-")


def audit(movies):
    """Filas cuya URI dice otra película. `movies`: objetos con id, tmdb_id, title,
    original_title y letterboxd_uri. Devuelve (seguras, compartidas, slugs)."""
    sure, slugs = [], []
    by_uri = collections.defaultdict(list)
    for m in movies:
        uri = (m.letterboxd_uri or "").strip()
        if not uri:
            continue
        by_uri[uri].append(m)
        n = CSVParser.tmdb_id_from_letterboxd_uri(uri)
        if n is not None:
            if n != m.tmdb_id:
                sure.append((m, n))
            continue
        if (s := SLUG_RE.search(uri)):
            slug = re.sub(r"-(19|20)\d\d(-\d+)?$", "", s.group(1))
            ratio = max(difflib.SequenceMatcher(None, slug, _slugify(t)).ratio()
                        for t in (m.title, m.original_title) if t)
            if ratio < SLUG_MIN_RATIO:
                slugs.append((ratio, m, s.group(1)))
    shared = [v for v in by_uri.values() if len(v) > 1]
    return sure, shared, slugs


async def reconcile(fix: bool) -> int:
    async with AsyncSessionLocal() as db:
        movies = (await db.execute(select(Movie))).scalars().all()
        ratings = (await db.execute(select(UserRating))).scalars().all()
        by_tmdb = {m.tmdb_id: m for m in movies}
        owned = collections.defaultdict(list)
        for r in ratings:
            owned[r.movie_id].append(r)

        sure, shared, slugs = audit(movies)
        print(f"{len(movies)} películas · URI de otra película (seguro): {len(sure)} · "
              f"URI compartida: {len(shared)} · slug que no cuadra: {len(slugs)}")
        for m, n in sure:
            right = by_tmdb.get(n)
            print(f"  [seguro] {m.title} ({m.year}, tmdb {m.tmdb_id}) lleva la URI de "
                  f"{right.title if right else '¿no está en el catálogo?'} (tmdb {n}) · "
                  f"valoraciones {len(owned[m.id])}")
        for group in shared:
            print("  [compartida] " + " | ".join(
                f"{m.title} ({m.year}, tmdb {m.tmdb_id}, valoraciones {len(owned[m.id])})" for m in group))
        for ratio, m, slug in sorted(slugs, key=lambda x: x[0]):
            print(f"  [slug {ratio:.2f}] {slug} → {m.title} ({m.year}) · valoraciones {len(owned[m.id])}")

        if not fix:
            return len(sure)
        moved = 0
        for m, n in sure:
            right = by_tmdb.get(n)
            if right is None:
                continue
            have = {r.user_id for r in owned[right.id]}
            for r in owned[m.id]:
                if r.user_id in have:
                    print(f"  u{r.user_id} ya tiene {right.title}: no se fusiona, revisar a mano")
                    continue
                r.movie_id = right.id
                moved += 1
            if not right.letterboxd_uri:
                right.letterboxd_uri = m.letterboxd_uri
            m.letterboxd_uri = f"https://letterboxd.com/tmdb/{m.tmdb_id}"
        await db.commit()
        print(f"--fix: {moved} valoraciones movidas, {len(sure)} URIs devueltas a su fila")
        return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="¿Es cada película la que dice su URI de Letterboxd?")
    ap.add_argument("--fix", action="store_true",
                    help="arregla el caso seguro (URI tmdb/N); sin esto, sólo informa")
    sys.exit(1 if asyncio.run(reconcile(ap.parse_args().fix)) else 0)


if __name__ == "__main__":
    main()
