"""¿Arregla el centrado las dos poblaciones de cosenos? — 2026-08-11

`utils/scoring.py` tiene DOS rectas porque los cosenos viven en dos sitios:

    consulta → película   0.48 – 0.62
    película → película   0.69 – 0.85, con el ruido en 0.473

Nada de eso es una propiedad del catálogo: dos películas SIN RELACIÓN están a
0.473 porque todos los vectores comparten una dirección media grande
(anisotropía). El coseno mide sobre todo esa dirección común, y lo que queda
para el tema es un margen estrecho. De ahí sale la torre entera: umbrales
absolutos que no cortan nada, dos escalas de normalización, y tres
comprobaciones ciegas para tocar una constante.

La pregunta, y sólo esta:

    RESTANDO LA MEDIA DEL CATÁLOGO, ¿SE SEPARAN LAS DOS POBLACIONES?

Si el ruido se va a ~0 y los vecinos reales se quedan arriba, un solo umbral
absoluto vuelve a significar algo y la mitad de `utils/scoring.py` sobra. Si no,
el centrado no es la respuesta y las dos rectas se quedan donde están.

RESPUESTA (2026-08-11, 21.228 vectores, 2000 pares × 4 semillas):

  NO. El centrado hace lo que promete con el CERO — la media del ruido pasa de
  +0.477 a −0.003 — pero no separa mejor lo suficiente para pagar lo que cuesta:

    d' (pareado, 4 semillas)   3.82 → 4.24    +0.42, mismo signo las 4 veces
    ruido sobre el peor vecino  1.5% → 1.4%    dentro del ruido del banco
    solape del top-20                 76-78%   RE-ORDENA una cuarta parte

  Un +0.4 de d' no vale un re-indexado completo + re-clusterizado + re-baseline
  del golden set, que es lo que cuesta reordenar el 24% de cada fila. Y el
  solape en las colas, que es lo único que un umbral necesita, no mejora.

LO QUE SÍ SALIÓ DE AQUÍ, y no era la pregunta: las anclas de ruido de
`utils/scoring.py` están medidas con pocas muestras. Con 120 pares el máximo era
0.694 y de ahí salió `FILM_NOISE_TOP = 0.695` ("0/120 llegan a 0.70"). Con 2000
pares × 4 semillas el p99 del ruido es ~0.70 y el máximo llega a 0.783 — que la
escala pinta como un 90 en la fila "más como esto". Las dos poblaciones SE
SOLAPAN en las colas; el hueco medido cruza el cero según la semilla. Re-anclar
exige las tres comprobaciones que documenta CLAUDE.md.

NO re-indexa nada. Lee los vectores que ya hay, centra en memoria y compara
PAREADO: los mismos pares, crudos y centrados. Sin Groq, sin red, semilla fija.

    docker compose exec backend python scripts/experiment_centering.py
    docker compose exec backend python scripts/experiment_centering.py --pairs 500
"""
import argparse
import asyncio
import logging
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from services.qdrant_service import QdrantService

logging.getLogger().setLevel(logging.ERROR)

SEED = 20260811


def _cos(a: np.ndarray, b: np.ndarray) -> float:
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))


def _pct(xs, p):
    return float(np.percentile(xs, p))


def _report(nombre, ruido, vecinos):
    """Las dos poblaciones y cuánto se separan.

    El hueco EN CRUDO (p05 − p99) no sirve para comparar centrado contra sin
    centrar: al centrar, todos los cosenos se encogen, así que el hueco encoge
    con ellos aunque la separación real no cambie. Comparar dos huecos medidos
    en escalas distintas es la regla de "anclar la métrica" de CLAUDE.md
    incumplida. Lo que sí compara es el solape: qué fracción del ruido supera
    al peor vecino real. Eso no tiene unidades y es exactamente lo que decide si
    un umbral puede existir.
    """
    ruido, vecinos = np.asarray(ruido), np.asarray(vecinos)
    hueco = _pct(vecinos, 5) - _pct(ruido, 99)
    # d' = separación en desviaciones típicas. Sin unidades, así que sobrevive
    # al cambio de escala que invalida el hueco.
    d = (vecinos.mean() - ruido.mean()) / np.sqrt((vecinos.var() + ruido.var()) / 2)
    solape = float((ruido > _pct(vecinos, 5)).mean())
    print(f"\n  {nombre}")
    print(f"    ruido    media {ruido.mean():+.3f}  p99 {_pct(ruido, 99):+.3f}  max {ruido.max():+.3f}")
    print(f"    vecinos  p05   {_pct(vecinos, 5):+.3f}  p50 {_pct(vecinos, 50):+.3f}  max {vecinos.max():+.3f}")
    print(f"    hueco {hueco:+.3f} (escala propia)   d' {d:.2f}   "
          f"ruido por encima del peor vecino: {solape:.1%}")
    return d, solape


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", type=int, default=300, help="pares independientes al azar")
    ap.add_argument("--seeds", type=int, default=20, help="películas de las que sacar vecinos")
    ap.add_argument("--topk", type=int, default=20)
    ap.add_argument("--seed", type=int, default=SEED,
                    help="cambiar sólo esto mide el suelo de ruido del propio banco")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    qdrant = QdrantService()

    # 1. Todo el catálogo en memoria. <10k puntos × 768 float32 = ~30 MB.
    print("Leyendo vectores…")
    vecs, offset = {}, None
    while True:
        points, offset = await qdrant.client.scroll(
            collection_name=QdrantService.COLLECTION_NAME,
            limit=1000, offset=offset, with_vectors=True, with_payload=False,
        )
        for p in points:
            dense = QdrantService._dense_of(p.vector)
            if dense is not None:
                vecs[p.id] = np.asarray(dense, dtype=np.float32)
        if offset is None:
            break
    await qdrant.aclose()

    ids = sorted(vecs)
    M = np.stack([vecs[i] for i in ids])
    media = M.mean(axis=0)
    print(f"  {len(ids)} vectores, dim {M.shape[1]}")
    # La norma de la media ES la anisotropía: 0 sería un espacio isótropo, y
    # cerca de 1 significa que todos los vectores apuntan casi al mismo sitio.
    print(f"  ‖media‖ = {np.linalg.norm(media):.3f}  (0 = isótropo, 1 = todos iguales)")

    C = {i: vecs[i] - media for i in ids}

    # 2. Ruido: pares INDEPENDIENTES, dos películas distintas cada vez. Colgar N
    #    pares de una sola base mide lo hub que es esa base, no el suelo del
    #    espacio (el error que documenta utils/scoring.py: 0.561 en vez de 0.473).
    ruido_crudo, ruido_centrado = [], []
    for _ in range(args.pairs):
        a, b = rng.sample(ids, 2)
        ruido_crudo.append(_cos(vecs[a], vecs[b]))
        ruido_centrado.append(_cos(C[a], C[b]))

    # 3. Vecinos reales: top-k de cada semilla POR EL ESPACIO CRUDO, que es el que
    #    está en producción. Los mismos pares se re-miden centrados — pareado.
    semillas = rng.sample(ids, args.seeds)
    vec_crudo, vec_centrado, solape = [], [], []
    for s in semillas:
        sims = M @ vecs[s] / (np.linalg.norm(M, axis=1) * np.linalg.norm(vecs[s]))
        orden = np.argsort(-sims)
        top_crudo = [ids[j] for j in orden if ids[j] != s][: args.topk]

        Cm = np.stack([C[i] for i in ids])
        sims_c = Cm @ C[s] / (np.linalg.norm(Cm, axis=1) * np.linalg.norm(C[s]))
        orden_c = np.argsort(-sims_c)
        top_centrado = [ids[j] for j in orden_c if ids[j] != s][: args.topk]

        for t in top_crudo:
            vec_crudo.append(_cos(vecs[s], vecs[t]))
            vec_centrado.append(_cos(C[s], C[t]))
        # Si el centrado reordena el top-20, esto no es un cambio de escala: es
        # otro ranking, y habría que revalidar el golden set y re-clusterizar.
        solape.append(len(set(top_crudo) & set(top_centrado)) / args.topk)

    print(f"\n{args.pairs} pares al azar · {args.seeds} semillas × top-{args.topk} vecinos")
    d_crudo, s_crudo = _report("CRUDO (producción)", ruido_crudo, vec_crudo)
    d_centrado, s_centrado = _report("CENTRADO (v − media)", ruido_centrado, vec_centrado)

    print(f"\n  solape del top-{args.topk} crudo vs centrado: "
          f"{np.mean(solape):.1%} (1.0 = mismo ranking, sólo cambia la escala)")
    print(f"\n  SEPARABILIDAD  d' {d_crudo:.2f} → {d_centrado:.2f}   "
          f"ruido sobre el peor vecino {s_crudo:.1%} → {s_centrado:.1%}")
    if abs(d_centrado - d_crudo) < 0.15:
        print("  → el centrado NO separa mejor. Mueve el cero, no la señal.")
    else:
        print(f"  → el centrado {'SEPARA MEJOR' if d_centrado > d_crudo else 'SEPARA PEOR'}.")


if __name__ == "__main__":
    asyncio.run(main())
