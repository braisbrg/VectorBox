"""¿Qué modelo y qué receta de embedding, medido con datos propios? — 2026-11-03.

Nació para una pregunta: EmbeddingGemma define prompts de tarea en su
`config_sentence_transformers.json` (consulta «task: search result | query: », documento
«title: none | text: »), no tiene ninguno por defecto, y `services/embedding_service.py` codifica
con `model.encode(texto)` a secas. Creció a comparar también otro modelo y otra receta, porque los
modelos envejecen rápido y la pregunta de fondo es si la forma de hacerlo es la mejor.

Todo es PAREADO (mismas películas, mismas consultas) y en memoria: no escribe nada en Qdrant ni en
Postgres. Los vectores de cada variante se guardan en `--cache`, así que una pasada cortada no se
repite.

Variantes del lado del catálogo:
  A  Gemma, `cinematic_description`, sin prompt  (lo que hay en producción)
  B  Gemma, `cinematic_description`, prompt `document`
  R  Gemma, receta cruda (sinopsis + géneros + keywords), sin prompt
  Q  Qwen3-Embedding-0.6B, `cinematic_description`, sin instrucción (así se usa en documentos)

Combinaciones consulta → película: a = A con consulta sin prompt (hoy) · b = B con prompt `query` ·
c = A con prompt `query` (no obliga a re-embeber) · r = R sin prompts · q = Q con la instrucción de
consulta de Qwen.

Medidas:
  0. Control: A contra el vector guardado en Qdrant (si no cuadra, no se mide producción).
  1. Anisotropía: coseno medio entre los mismos pares al azar, por variante.
  2. Consulta → película: las 12 consultas del golden set (`tests/fixtures/search_relevance.py`)
     con sus filtros de país y año; nDCG@10 y Recall@20 por consulta y un orden al azar como
     ancla. Mide SÓLO la etapa de embedding: en producción van encima el gate de calidad, el
     acantilado y el canal léxico.
  3. Película → película: las anclas de `tests/test_embeddings_golden_set.py` con su métrica
     (géneros compartidos y directores distintos en el top-10), más los títulos para mirarlos.

Resultado (2026-11-03, 5.000 películas): ninguna variante gana con claridad, así que se queda A. El
control 0 fue el que destapó las bibliotecas con vectores de la receta cruda.

Lee Postgres y Qdrant, así que necesita el stack. `/out` es una carpeta del host montada a mano: con
`--rm`, la caché y el JSON se perderían dentro del contenedor. Con `qwen`, el modelo se baja de
HuggingFace la primera vez.

    docker compose run --rm -v CARPETA_DEL_HOST:/out backend \
        python scripts/experiment_embedding_prompts.py \
        --limit 5000 --models gemma,qwen --cache /out/cache --out /out/r.json
"""
import argparse
import asyncio
import gc
import hashlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from sqlalchemy import select

from config import AsyncSessionLocal
from models.database import Movie
from scripts.eval_search import ndcg_at
from services.embedding_service import get_model
from services.qdrant_service import QdrantService
from tests.fixtures.search_relevance import GRADES, QUERIES
from tests.test_embeddings_golden_set import _ANCHORS, _TOP_K

PAYLOAD = ["title", "year", "countries", "genres", "directors", "has_enriched_embedding", "is_adult"]
EXAMPLES = ("duelo", "giallo italiano", "cyberpunk", "found footage 60s")
QWEN = "Qwen/Qwen3-Embedding-0.6B"

# variante: (modelo, receta, prompt de documento)
DOC_VARIANTS = {
    "A": ("gemma", "desc", None),
    "B": ("gemma", "desc", "document"),
    "R": ("gemma", "raw", None),
    "Q": ("qwen", "desc", None),
}
# combinación: (variante de catálogo, modelo de la consulta, prompt de consulta)
COMBOS = {
    "a": ("A", "gemma", None),
    "b": ("B", "gemma", "query"),
    "c": ("A", "gemma", "query"),
    "r": ("R", "gemma", None),
    "q": ("Q", "qwen", "query"),
}


def raw_recipe(m) -> str:
    """La receta de reserva de EmbeddingService.generate_batch_embeddings, sin título."""
    parts = []
    if m.overview:
        parts.append(m.overview)
    if m.genres:
        parts.append(f"Genres: {', '.join(m.genres)}")
    if m.keywords:
        parts.append(f"Themes: {', '.join(m.keywords[:15])}")
    return ". ".join(parts) or (m.title or "Unknown")


async def load_pool(limit: int, seed: int):
    qd = QdrantService()
    points, offset = [], None
    while True:
        batch, offset = await qd.client.scroll(
            collection_name=qd.COLLECTION_NAME, limit=1024, offset=offset,
            with_payload=PAYLOAD, with_vectors=True,
        )
        points += batch
        if offset is None:
            break
    # La misma población que ve search_similar por defecto: enriquecidas y no adultas.
    gated = {int(p.id): p for p in points
             if p.payload.get("has_enriched_embedding") and not p.payload.get("is_adult")}
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(Movie.tmdb_id, Movie.cinematic_description, Movie.title, Movie.overview,
                   Movie.genres, Movie.keywords, Movie.is_excluded)
            .where(Movie.tmdb_id.in_(list(gated)))
        )).all()
    meta = {r.tmdb_id: r for r in rows if r.cinematic_description and not r.is_excluded}
    ids = sorted(meta)

    if limit and limit < len(ids):
        # Siempre dentro: todo lo etiquetado y las anclas, para que la muestra no las pierda.
        anchors = {a.lower() for a in _ANCHORS}
        must = {i for g in GRADES.values() for i in g}
        must |= {r.tmdb_id for r in meta.values() if (r.title or "").lower() in anchors}
        must &= set(ids)
        rest = [i for i in ids if i not in must]
        rng = np.random.default_rng(seed)
        fill = rng.choice(rest, size=max(0, limit - len(must)), replace=False).tolist()
        ids = sorted(must | set(fill))

    stored = np.array([QdrantService._dense_of(gated[i].vector) for i in ids], dtype=np.float32)
    stored /= np.linalg.norm(stored, axis=1, keepdims=True)
    return ids, meta, {i: gated[i].payload for i in ids}, stored, len(points)


class Models:
    def __init__(self):
        self.name, self.model = None, None

    def get(self, name):
        if name != self.name:
            self.model = None
            gc.collect()
            self.model = get_model() if name == "gemma" else __import__(
                "sentence_transformers").SentenceTransformer(QWEN)
            self.name = name
        return self.model


def encode_docs(models, key, texts, cache_dir, tag):
    path = os.path.join(cache_dir, f"{key}_{tag}.npy") if cache_dir else None
    if path and os.path.exists(path):
        print(f"  {key}: de la caché ({path})")
        return np.load(path), 0.0
    model_name, _, prompt = DOC_VARIANTS[key]
    t = time.time()
    v = models.get(model_name).encode(
        texts, batch_size=32, normalize_embeddings=True, convert_to_numpy=True,
        **({"prompt_name": prompt} if prompt else {})).astype(np.float32)
    took = time.time() - t
    if path:
        np.save(path, v)
    print(f"  {key}: codificado en {took:.0f} s")
    return v, took


def random_pair_cos(v, n=50_000):
    rng = np.random.default_rng(0)
    i, j = rng.integers(0, len(v), n), rng.integers(0, len(v), n)
    keep = i != j
    return float(np.mean(np.sum(v[i[keep]] * v[j[keep]], axis=1)))


def query_rows(models, ids, payload, docs, titles, combos):
    pos = np.array(ids)
    years = np.array([payload[i].get("year") or 0 for i in ids])
    qvec = {}
    for name in combos:
        _, model_name, prompt = COMBOS[name]
        if (model_name, prompt) not in qvec:
            m = models.get(model_name)
            qvec[(model_name, prompt)] = {
                label: m.encode(kw["semantic_query"], normalize_embeddings=True,
                                **({"prompt_name": prompt} if prompt else {}))
                for label, kw in QUERIES.items()}
    rows, examples = [], {}
    for label, kw in QUERIES.items():
        mask = np.ones(len(ids), dtype=bool)
        if kw.get("year_min"):
            mask &= years >= kw["year_min"]
        if kw.get("year_max"):
            mask &= years <= kw["year_max"]
        if kw.get("countries"):
            want = set(kw["countries"])
            mask &= np.array([bool(want & set(payload[i].get("countries") or [])) for i in ids])
        cand = np.flatnonzero(mask)
        graded = GRADES[label]
        ideal = list(graded.values())
        relevant = {i for i, g in graded.items() if g >= 1}
        row = {"query": label, "pool": int(len(cand))}
        for name in combos:
            doc_key, model_name, prompt = COMBOS[name]
            q = qvec[(model_name, prompt)][label]
            order = cand[np.argsort(-(docs[doc_key][cand] @ q))]
            got = pos[order].tolist()
            row[f"ndcg_{name}"] = ndcg_at([graded.get(i, 0) for i in got], ideal, 10)
            row[f"recall_{name}"] = len(relevant & set(got[:20])) / len(relevant) if relevant else 0.0
            if label in EXAMPLES:
                examples.setdefault(label, {})[name] = [f"[{graded.get(i, 0)}] {titles[i]}" for i in got[:10]]
        rng = np.random.default_rng(1)
        row["ndcg_random"] = float(np.mean([
            ndcg_at([graded.get(i, 0) for i in pos[rng.permutation(cand)][:10]], ideal, 10)
            for _ in range(100)]))
        rows.append(row)
    return rows, examples


def anchor_rows(ids, payload, docs, titles):
    rows = []
    lower = {(titles[i] or "").lower(): k for k, i in enumerate(ids)}
    for anchor in _ANCHORS:
        k = lower.get(anchor.lower())
        if k is None:
            rows.append({"anchor": anchor, "missing": True})
            continue
        a_genres = set(payload[ids[k]].get("genres") or [])
        row = {"anchor": anchor}
        for key, v in docs.items():
            hits = [ids[j] for j in np.argsort(-(v @ v[k])) if j != k][:_TOP_K]
            row[f"genre_frac_{key}"] = sum(
                1 for h in hits if a_genres & set(payload[h].get("genres") or [])) / len(hits)
            row[f"directors_{key}"] = len({d for h in hits for d in (payload[h].get("directors") or [])})
            row[f"top_{key}"] = [titles[h] for h in hits]
        rows.append(row)
    return rows


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0, help="tamaño del pool (0 = catálogo entero)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--models", default="gemma", help="gemma, qwen o gemma,qwen")
    ap.add_argument("--cache", default="", help="carpeta donde guardar y reutilizar los vectores")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    wanted = set(args.models.split(","))

    ids, meta, payload, stored, n_points = await load_pool(args.limit, args.seed)
    tag = f"{len(ids)}_{args.seed}_{hashlib.sha1(','.join(map(str, ids)).encode()).hexdigest()[:10]}"
    titles = {i: meta[i].title for i in ids}
    texts = {"desc": [meta[i].cinematic_description for i in ids],
             "raw": [raw_recipe(meta[i]) for i in ids]}
    print(f"Qdrant: {n_points} puntos · pool medido: {len(ids)} películas · pool {tag}")
    if args.cache:
        os.makedirs(args.cache, exist_ok=True)

    models = Models()
    docs, seconds = {}, {}
    for key, (model_name, recipe, _) in DOC_VARIANTS.items():
        if model_name in wanted:
            docs[key], seconds[key] = encode_docs(models, key, texts[recipe], args.cache, tag)
    combos = [c for c, (doc_key, _, _) in COMBOS.items() if doc_key in docs]

    drift = np.sum(docs["A"] * stored, axis=1) if "A" in docs else None
    control = None if drift is None else {
        "mean": float(drift.mean()), "p05": float(np.percentile(drift, 5)),
        "min": float(drift.min()), "below_0.99": int((drift < 0.99).sum())}
    if control:
        print(f"\n0. control, A contra lo guardado en Qdrant: coseno medio {control['mean']:.4f} · "
              f"p05 {control['p05']:.4f} · mínimo {control['min']:.4f} · "
              f"{control['below_0.99']} de {len(ids)} por debajo de 0,99")

    aniso = {"guardado": random_pair_cos(stored), **{k: random_pair_cos(v) for k, v in docs.items()}}
    print("\n1. coseno medio entre pares al azar: " + " · ".join(f"{k} {v:.3f}" for k, v in aniso.items()))

    qrows, examples = query_rows(models, ids, payload, docs, titles, combos)
    print("\n2. consulta -> película, nDCG@10 (a hoy · b Gemma con los dos prompts · c sólo consulta · "
          "r receta cruda · q Qwen3-0.6B)")
    print(f"  {'consulta':<20} {'pool':>6} {'azar':>6} " + " ".join(f"{c:>6}" for c in combos))
    for r in qrows:
        print(f"  {r['query']:<20} {r['pool']:>6} {r['ndcg_random']:>6.3f} "
              + " ".join(f"{r['ndcg_' + c]:>6.3f}" for c in combos))
    print("  Recall@20: " + " · ".join(
        f"{c} {np.mean([r['recall_' + c] for r in qrows]):.3f}" for c in combos))
    for c in combos:
        if c == "a":
            continue
        diffs = [r[f"ndcg_{c}"] - r["ndcg_a"] for r in qrows]
        up, down = sum(d > 0.005 for d in diffs), sum(d < -0.005 for d in diffs)
        print(f"  {c} - a: media {np.mean(diffs):+.3f} · mejoran {up} · empeoran {down} · "
              f"empatan {len(diffs) - up - down}")
    for label, ex in examples.items():
        print(f"\n  «{label}», top-10 por combinación:")
        for c in combos:
            print(f"    {c}: " + " | ".join(t[:30] for t in ex[c]))

    arows = anchor_rows(ids, payload, docs, titles)
    print("\n3. película -> película (géneros compartidos / directores distintos en el top-10)")
    for r in arows:
        if r.get("missing"):
            print(f"  {r['anchor']:<18} no está en el pool")
            continue
        print(f"  {r['anchor']:<18} " + " · ".join(
            f"{k} {r['genre_frac_' + k]:.2f}/{r['directors_' + k]:>2}" for k in docs))
        for k in docs:
            print(f"      {k}: {', '.join(t[:22] for t in r['top_' + k][:6])}")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump({"pool": len(ids), "tag": tag, "control": control, "anisotropy": aniso,
                       "queries": qrows, "examples": examples, "anchors": arows,
                       "seconds": seconds}, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
