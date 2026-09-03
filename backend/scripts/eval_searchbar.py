"""Banco de relevancia de la SEARCHBAR (modo título).

Contra el endpoint REAL por HTTP, no contra la función: el orden final depende del
rescate de pósters, del corte de ocho y de la fusión con la filmografía del
director, y llamar a la función por dentro se saltaría parte de eso.

    docker compose exec backend python scripts/eval_searchbar.py
    docker compose exec backend python scripts/eval_searchbar.py --json

Métrica: posición 1 sobre todo (en un autocompletado el primer sitio es casi todo
el producto), luego top-3, y top-8 porque ocho es lo que caben. MRR para tener un
número único con el que comparar dos versiones.

No es un test de pytest: necesita TMDB en vivo y la suite es hermética a propósito
(ver pytest.ini). Se corre a mano cuando se toca el ranking.
"""
import argparse
import json
import sys
import urllib.parse
import urllib.request
from collections import defaultdict

sys.path.insert(0, "/app")

from tests.fixtures.searchbar_cases import CASES, DIRECTOR_CASES, JUNK_CASES

BASE = "http://localhost:8000/api/search/autocomplete"


def ask(q: str):
    url = f"{BASE}?q={urllib.parse.quote(q)}"
    with urllib.request.urlopen(url, timeout=30) as r:
        d = json.load(r)
    # El endpoint devolvió una lista plana antes de la tarjeta de director; se
    # aceptan las dos formas para que el banco no muera con un despliegue viejo.
    if isinstance(d, list):
        return None, d
    return d.get("director"), (d.get("films") or [])


def rank_of(films, accept) -> int:
    """1-indexado; 0 = no está en la respuesta."""
    for i, f in enumerate(films, 1):
        if f.get("tmdb_id") in accept:
            return i
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    por_cat = defaultdict(list)
    fallos = []
    filas = []

    for q, accept, cat, nota in CASES:
        director, films = ask(q)
        r = rank_of(films, accept)
        por_cat[cat].append(r)
        filas.append({"query": q, "cat": cat, "rank": r, "n": len(films), "nota": nota})
        if r != 1:
            fallos.append((q, cat, r, [f"{f.get('title')} ({f.get('year')})" for f in films[:3]], nota))

    dir_ok, dir_fallos = 0, []
    for q, nombre, accept in DIRECTOR_CASES:
        director, films = ask(q)
        tiene_tarjeta = (director or {}).get("name") == nombre
        r = rank_of(films, accept)
        if tiene_tarjeta and r:
            dir_ok += 1
        else:
            dir_fallos.append((q, (director or {}).get("name"), nombre, r))

    junk_fallos = []
    for q in JUNK_CASES:
        try:
            director, films = ask(q)
            if not isinstance(films, list):
                junk_fallos.append((q, f"films no es lista: {type(films)}"))
        except Exception as e:
            junk_fallos.append((q, repr(e)))

    todos = [f["rank"] for f in filas]
    n = len(todos)
    top1 = sum(1 for r in todos if r == 1)
    top3 = sum(1 for r in todos if 1 <= r <= 3)
    top8 = sum(1 for r in todos if r >= 1)
    mrr = sum(1 / r for r in todos if r) / n

    if args.json:
        print(json.dumps({
            "n": n, "top1": top1, "top3": top3, "top8": top8, "mrr": round(mrr, 4),
            "por_categoria": {c: rs for c, rs in por_cat.items()},
            "directores_ok": dir_ok, "directores_total": len(DIRECTOR_CASES),
            "junk_fallos": junk_fallos, "filas": filas,
        }, ensure_ascii=False, indent=2))
        return

    print(f"\n  {n} consultas de título\n")
    print("  %-26s %-10s %-6s %s" % ("consulta", "categoria", "puesto", "nota"))
    for f in filas:
        marca = "" if f["rank"] == 1 else ("  <-- FUERA" if not f["rank"] else "  <--")
        print("  %-26s %-10s %-6s %s%s" % (
            f["query"][:26], f["cat"], f["rank"] or "--", f["nota"][:34], marca))

    print("\n  por categoria (puesto 1 / total):")
    for cat, rs in por_cat.items():
        print("    %-10s %d/%d   puestos %s" % (
            cat, sum(1 for r in rs if r == 1), len(rs), [r or "-" for r in rs]))

    print(f"\n  puesto 1: {top1}/{n} ({100*top1/n:.0f}%)   "
          f"top-3: {top3}/{n}   en el desplegable: {top8}/{n}   MRR: {mrr:.3f}")
    print(f"  directores: {dir_ok}/{len(DIRECTOR_CASES)} con tarjeta + filmografía dentro")
    print(f"  entradas basura: {len(JUNK_CASES) - len(junk_fallos)}/{len(JUNK_CASES)} sin romper nada")

    if fallos:
        print("\n  FALLOS de posición 1:")
        for q, cat, r, top, nota in fallos:
            print(f"    {q!r} ({cat}) puesto={r or 'fuera'}  {nota}")
            for i, t in enumerate(top, 1):
                print(f"        {i}. {t}")
    for q, got, want, r in dir_fallos:
        print(f"\n  FALLO director {q!r}: tarjeta={got!r} esperada={want!r} filmografia_puesto={r or 'fuera'}")
    for q, err in junk_fallos:
        print(f"\n  FALLO basura {q!r}: {err}")


main()
