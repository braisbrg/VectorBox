"""¿Sirve una señal INTER-director? Hold-out dejando fuera un director entero.

## Por qué este diseño y no el del banco de la Señal A

`bench_synthetic_profiles.py` esconde PELÍCULAS y mira si el recomendador las recupera.
Eso no puede evaluar esto: las películas escondidas son de directores que el usuario ya
ama, así que las recupera la Señal B (intra-director) por construcción. Lo que una señal
inter-director promete es otra cosa — **saltar a un director que NO has visto** — y para
medir eso hay que esconder al director entero.

Fold = (usuario, director amado D): se borra D del perfil, se construye la afinidad con
los directores que quedan, y se mira **en qué puesto aparece D** de entre todos los
candidatos. Cuanto más arriba, mejor.

## Los controles, que es lo que hace legible el número

  · **azar** — ancla obligatoria. Un ranking aleatorio tiene que dar rango mediano ≈ n/2.
    Si no lo da, el banco está roto y ningún otro número significa nada.
  · **popularidad** — el control que de verdad duele. "Recomienda directores famosos"
    acierta muchísimo sin entender nada, porque la gente ve a los famosos. Una señal que
    no le gane a esto no está aportando afinidad: está aportando fama.

Se compara **pareado** (mismo fold para los tres métodos) y se promedian las diferencias,
que es lo que cancela la dificultad del caso. Media contra media aquí no vale: unos
directores son intrínsecamente más fáciles de acertar que otros.

## Anti-circularidad

Los centroides se construyen sobre vectores CENTRADOS (α=0.5). Sin centrar, la componente
común anisótropa domina y la coherencia de un director famoso queda a +0.034 del ruido
(medido 2026-08-19) — que fue exactamente el error que dio por muerta esta vía.

    docker compose exec backend python scripts/bench_inter_director.py
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from sqlalchemy import text

from config import AsyncSessionLocal
from services.mood_axes import CENTERING_ALPHA, center
from services.qdrant_service import QdrantService
from scripts.compute_mood_axes import load_catalogue

MIN_FILMS_DIRECTOR = 4      # menos que esto y el centroide lo definen 3 películas
MIN_AMADAS = 2              # cuántas suyas tiene que haber puntuado 4+ para contar como amado
BANDA_FAMA = 200            # tamaño de la banda de fama para la evaluación estratificada
MIN_DIRECTORES = 4          # perfiles con menos no dan folds útiles


async def _cargar():
    qd = QdrantService()
    ids, X = await load_catalogue(qd)
    pos = {t: i for i, t in enumerate(ids)}
    # ABTT k=1 (Mu & Viswanath 2018) en vez de centrar a medias: medido 2026-08-19,
    # separa la coherencia de directores famosos del ruido 7,0x frente a 2,1x del
    # centrado alpha=0.5, y deja el suelo de ruido en 0,000 exacto. k=7 (el d/100 que
    # recomienda el paper) es PEOR aqui: este espacio tiene UNA direccion dominante.
    Y = X - X.mean(axis=0)
    _, _, Vt = np.linalg.svd(Y, full_matrices=False)
    Y = Y - np.outer(Y @ Vt[0], Vt[0])
    Xc = Y / np.linalg.norm(Y, axis=1, keepdims=True)

    async with AsyncSessionLocal() as db:
        filas = (await db.execute(text(
            """select unnest(directors) d, array_agg(tmdb_id) t from movies
               where directors is not null group by 1 having count(*) >= :n"""
        ), {"n": MIN_FILMS_DIRECTOR})).all()
        fama = dict((d, n) for d, n in (await db.execute(text(
            """select unnest(directors) d, sum(vote_count) n from movies
               where directors is not null group by 1"""
        ))).all())

    nombres, cents = [], []
    for d, tids in filas:
        idx = [pos[t] for t in tids if t in pos]
        if len(idx) < MIN_FILMS_DIRECTOR:
            continue
        mu = Xc[idx].mean(axis=0)
        nombres.append(d)
        cents.append(mu / np.linalg.norm(mu))
    return nombres, np.stack(cents), fama


async def _perfiles(nombres):
    """{user_id: [directores amados presentes en el grafo]}"""
    conocidos = set(nombres)
    async with AsyncSessionLocal() as db:
        filas = (await db.execute(text(
            """select ur.user_id, unnest(m.directors) d, count(*) n
               from user_ratings ur join movies m on m.id = ur.movie_id
               where ur.rating >= 4.0 group by 1, 2 having count(*) >= :k"""
        ), {"k": MIN_AMADAS})).all()
    out: dict[int, list[str]] = {}
    for uid, d, _ in filas:
        if d in conocidos:
            out.setdefault(uid, []).append(d)
    return {u: ds for u, ds in out.items() if len(ds) >= MIN_DIRECTORES}


def _rango(orden: np.ndarray, nombres: list[str], objetivo: str) -> int:
    """Puesto (1 = primero) del director escondido en un ranking ya ordenado."""
    for puesto, i in enumerate(orden, 1):
        if nombres[i] == objetivo:
            return puesto
    return len(nombres)


async def main() -> None:
    nombres, C, fama = await _cargar()
    perfiles = await _perfiles(nombres)
    idx = {d: i for i, d in enumerate(nombres)}
    pop = np.array([fama.get(d, 0) for d in nombres], dtype=float)
    rng = np.random.default_rng(7)

    print(f"grafo: {len(nombres)} directores con >={MIN_FILMS_DIRECTOR} películas")
    print(f"perfiles con >={MIN_DIRECTORES} directores amados: {sorted(perfiles)}\n")

    filas = []
    for uid, amados in sorted(perfiles.items()):
        for oculto in amados:
            resto = [d for d in amados if d != oculto]
            if len(resto) < 2:
                continue
            R = C[[idx[d] for d in resto]]
            # DOS formas de agregar, y la diferencia es el hallazgo:
            #   media  -> un solo vector. Destruye los modos: u210 ama a Fritz Lang Y a
            #             Disney, y el punto medio no apunta a ninguno de los dos.
            #   maximo -> se parece al MEJOR de tus directores, no al promedio de todos.
            mu = R.mean(axis=0); mu = mu / np.linalg.norm(mu)
            sim_media = C @ mu
            sim = (C @ R.T).max(axis=1)
            for d in resto:                      # los que ya tiene no compiten
                sim[idx[d]] = -np.inf
                sim_media[idx[d]] = -np.inf
            p = pop.copy()
            for d in resto:
                p[idx[d]] = -np.inf
            az = rng.random(len(nombres))
            for d in resto:
                az[idx[d]] = -np.inf
            filas.append((uid, oculto,
                          _rango(np.argsort(-sim), nombres, oculto),
                          _rango(np.argsort(-p), nombres, oculto),
                          _rango(np.argsort(-az), nombres, oculto),
                          _rango(np.argsort(-sim_media), nombres, oculto)))

    if not filas:
        print("sin folds — no hay perfiles suficientes")
        return

    afin = np.array([f[2] for f in filas], float)
    popu = np.array([f[3] for f in filas], float)
    azar = np.array([f[4] for f in filas], float)
    n = len(nombres)

    print(f"{len(filas)} folds (usuario × director escondido)\n")
    print(f"{'método':<14}{'rango mediano':>14}{'top-10':>9}{'top-50':>9}")
    media = np.array([f[5] for f in filas], float)
    for etq, v in (("afinidad MAX", afin), ("afinidad media", media), ("popularidad", popu), ("azar", azar)):
        print(f"{etq:<14}{np.median(v):>14.0f}{100*(v<=10).mean():>8.0f}%{100*(v<=50).mean():>8.0f}%")
    print(f"\nancla: el azar debe dar ~{n//2} (la mitad de {n}). "
          f"Da {np.median(azar):.0f} — {'OK' if abs(np.median(azar)-n/2) < n*0.15 else 'BANCO ROTO'}")

    # Pareado: la diferencia POR FOLD, que cancela la dificultad del caso.
    d = popu - afin
    from scripts.bench_signal_a_production import ci95
    # ci95 devuelve (media, SEMIANCHO), no (lo, hi). Leerlo como intervalo fue un
    # error real el 2026-08-19 que hizo pasar por "ambiguo" un resultado significativo.
    mu_d, h_d = ci95(d)
    print(f"\nafinidad vs popularidad, PAREADO sobre los mismos {len(filas)} folds:")
    print(f"   mejora media de puesto: {mu_d:+.0f}  IC95 [{mu_d-h_d:+.0f}, {mu_d+h_d:+.0f}]"
          f"  {'(cruza 0: no concluyente)' if abs(mu_d) < h_d else ''}")
    print(f"   gana afinidad en {100*(afin<popu).mean():.0f}% de los folds")

    # ---- Evaluacion ESTRATIFICADA, que es la honesta ----
    # El director escondido es uno que el usuario SI vio, y la gente ve a los famosos,
    # asi que rankear sobre los 1.586 le regala la victoria a la popularidad. Aqui cada
    # fold compite solo contra directores de FAMA PARECIDA: dentro de la banda, la
    # popularidad es casi aleatoria y lo que quede es afinidad de verdad.
    orden_fama = np.argsort(-pop)
    banda_de = {}
    for puesto, i in enumerate(orden_fama):
        banda_de[i] = puesto // BANDA_FAMA
    ga = gp = 0; difs = []
    for uid, oculto, _, _, _, _ in filas:
        amados = perfiles[uid]
        resto = [d for d in amados if d != oculto]
        if len(resto) < 2:
            continue
        R = C[[idx[d] for d in resto]]
        mu = R.mean(axis=0); mu = mu / np.linalg.norm(mu)
        b = banda_de[idx[oculto]]
        cand = [i for i in range(len(nombres)) if banda_de[i] == b and nombres[i] not in resto]
        if len(cand) < 20:
            continue
        sim = C[cand] @ mu
        pp = pop[cand]
        obj = cand.index(idx[oculto])
        ra = 1 + int((sim > sim[obj]).sum())
        rp = 1 + int((pp > pp[obj]).sum())
        difs.append(rp - ra); ga += ra < rp; gp += rp < ra
    difs = np.array(difs, float)
    mu2, h2 = ci95(difs)
    print("")
    print(f"=== ESTRATIFICADO por fama (bandas de {BANDA_FAMA}) — {len(difs)} folds ===")
    print(f"   afinidad gana {ga}, popularidad gana {gp}  ({100*ga/max(1,ga+gp):.0f}% para afinidad)")
    print(f"   mejora media de puesto: {mu2:+.1f}  IC95 [{mu2-h2:+.1f}, {mu2+h2:+.1f}]")
    print("   " + ("AFINIDAD APORTA (el intervalo no cruza 0)" if mu2 - h2 > 0
                   else "la FAMA gana" if mu2 + h2 < 0 else "no concluyente: el intervalo cruza 0"))

    peor = sorted(filas, key=lambda f: -f[2])[:5]
    print("\nlos 5 folds donde peor lo hace la afinidad:")
    for uid, oc, ra, rp, _, _m in peor:
        print(f"   u{uid} sin {oc[:26]:<26} afinidad #{ra:<6} popularidad #{rp}")


if __name__ == "__main__":
    asyncio.run(main())
