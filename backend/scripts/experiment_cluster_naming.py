"""Scout vs 70B A/B for cluster naming.

`clustering_service.py` asks an LLM to label a cluster of films in 2-4 words
(e.g. "Slow Burn Noir", "Korean Revenge Cinema"). Currently uses 70B as
primary after we switched away from Scout. This script generates the same
label with BOTH models on a handful of synthetic-but-realistic clusters,
side-by-side, so we can judge whether 70B is fine for this task or whether
Scout (or some other model) would give cleaner labels.

The 6 synthetic clusters cover different cluster shapes:
  - Coherent theme (Ghibli)
  - Coherent mood (slow-burn dramas)
  - Subgenre / movement (Korean revenge)
  - Director style (Tarantino-like crime)
  - Genuinely mixed (blockbuster mix)
  - Era + style (80s synth sci-fi)

Output: markdown side-by-side. No DB writes.
"""
import argparse
import asyncio
import logging
import os
import sys
import time
from pathlib import Path

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openai import AsyncOpenAI
from sqlalchemy import select

from config import AsyncSessionLocal
from models.database import Movie, UserCluster
from scripts.experiment_enricher_prompt import call_groq

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)


MODELS = {
    "scout": "meta-llama/llama-4-scout-17b-16e-instruct",
    "70b":   "llama-3.3-70b-versatile",
}


# Synthetic clusters mimicking what clustering_service.py would pass in.
# Each cluster has the same structure that the production prompt receives.
CLUSTERS = [
    {
        "id": "ghibli",
        "description": "Coherent theme (Studio Ghibli)",
        "films": [
            "Spirited Away (2001)", "My Neighbor Totoro (1988)", "Princess Mononoke (1997)",
            "Howl's Moving Castle (2004)", "Castle in the Sky (1986)", "Kiki's Delivery Service (1989)",
            "Ponyo (2008)", "The Wind Rises (2013)", "Porco Rosso (1992)", "Nausicaä (1984)",
        ],
        "genres": ["Animation", "Fantasy", "Adventure", "Family"],
        "stats": "10 films total | year span 1984-2013 | avg★ 4.50",
    },
    {
        "id": "slow-burn-drama",
        "description": "Coherent MOOD (slow-burn character dramas)",
        "films": [
            "Lost in Translation (2003)", "Manchester by the Sea (2016)",
            "The Tree of Life (2011)", "Paterson (2016)", "Roma (2018)",
            "Past Lives (2023)", "Aftersun (2022)", "Drive My Car (2021)",
            "Burning (2018)", "Moonlight (2016)",
        ],
        "genres": ["Drama"],
        "stats": "10 films total | year span 2003-2023 | avg★ 4.30",
    },
    {
        "id": "korean-revenge",
        "description": "Subgenre / national movement (Korean revenge cinema)",
        "films": [
            "Oldboy (2003)", "Lady Vengeance (2005)", "Sympathy for Mr. Vengeance (2002)",
            "I Saw the Devil (2010)", "The Chaser (2008)", "Memories of Murder (2003)",
            "The Man from Nowhere (2010)", "A Bittersweet Life (2005)",
        ],
        "genres": ["Thriller", "Crime", "Drama"],
        "stats": "8 films total | year span 2002-2010 | avg★ 4.45",
    },
    {
        "id": "tarantino-crime",
        "description": "Director style (Tarantino-like stylized crime)",
        "films": [
            "Pulp Fiction (1994)", "Reservoir Dogs (1992)", "Kill Bill: Vol. 1 (2003)",
            "Jackie Brown (1997)", "Once Upon a Time in Hollywood (2019)",
            "Inglourious Basterds (2009)", "Django Unchained (2012)",
        ],
        "genres": ["Crime", "Thriller", "Drama"],
        "stats": "7 films total | year span 1992-2019 | avg★ 4.60",
    },
    {
        "id": "blockbuster-mix",
        "description": "Genuinely MIXED (random blockbusters)",
        "films": [
            "Avatar (2009)", "Top Gun: Maverick (2022)", "Jurassic Park (1993)",
            "Avengers: Endgame (2019)", "Titanic (1997)", "The Dark Knight (2008)",
            "Frozen (2013)", "The Lion King (1994)",
        ],
        "genres": ["Action", "Adventure", "Drama", "Family"],
        "stats": "8 films total | year span 1993-2022 | avg★ 4.20",
    },
    {
        "id": "80s-synth-scifi",
        "description": "Era + visual style (80s synth-driven sci-fi)",
        "films": [
            "Blade Runner (1982)", "TRON (1982)", "The Terminator (1984)",
            "Aliens (1986)", "Akira (1988)", "Robocop (1987)",
            "Escape from New York (1981)", "They Live (1988)",
        ],
        "genres": ["Science Fiction", "Action", "Thriller"],
        "stats": "8 films total | year span 1981-1988 | avg★ 4.40",
    },
]


def build_prompt(cluster: dict) -> list[dict]:
    """Same prompt as clustering_service.py:189-208 (verbatim)."""
    titles_lines = "\n  - ".join(cluster["films"])
    genres_str = ", ".join(cluster["genres"])
    stats_str = cluster["stats"]
    prompt_text = (
        f"Films in this cluster (top by rating):\n  - {titles_lines}"
        f"\n\nDominant genres: {genres_str}"
        f"\nStats: {stats_str}"
        "\n\nName this cluster in 2-4 words (English). "
        "Examples of GOOD labels for coherent themes: "
        "'Slow Burn Noir', 'European Art House', '80s Synth Sci-Fi', "
        "'Studio Ghibli Wonder', 'Korean Revenge Cinema'. "
        "\n\nIMPORTANT: A cluster can span MANY decades and still have a "
        "coherent MOOD/STYLE (e.g. 'Quiet Character Drama', 'Visual Auteur Cinema', "
        "'Studio Ghibli Wonder'). Don't reject thematic labels just because of a "
        "wide year range — focus on whether the films share a common emotional "
        "register, visual style, or directorial sensibility. "
        "\n\nONLY if the films genuinely lack any common mood/theme/style "
        "(e.g. random blockbusters thrown together, or unrelated dramas that "
        "happen to be highly rated), return a HONEST GENERIC label like "
        "'Mixed Drama', 'Genre Crowd-Pleasers', '[Dominant Genre] Mix'. "
        "Do NOT confabulate a fake theme, but DO surface a real one when present. "
        "\n\nRespond with ONLY the label. No quotes, no explanation, no trailing punctuation."
    )
    return [
        {
            "role": "system",
            "content": (
                "You name film clusters honestly. Respond with ONLY a 2-4 word English label. "
                "No punctuation at the end. If films don't share a coherent theme, use a generic label."
            ),
        },
        {"role": "user", "content": prompt_text},
    ]


async def load_real_clusters(user_id: int) -> list[dict]:
    """Build cluster dicts from a real user's stored clusters. Uses
    `sample_movie_ids` (top 5 films per cluster as picked by the K-means
    pipeline) as the film list, joined with `Movie` for title/year. Includes
    the existing `cluster_label` so we can compare with the new generations.
    """
    async with AsyncSessionLocal() as db:
        clusters = (await db.execute(
            select(UserCluster).where(UserCluster.user_id == user_id).order_by(UserCluster.cluster_id)
        )).scalars().all()
        if not clusters:
            raise RuntimeError(f"No clusters found for user_id={user_id}. Run reset_profiles.py first.")
        out = []
        for c in clusters:
            sample_ids = c.sample_movie_ids or []
            if not sample_ids:
                continue
            rows = (await db.execute(
                select(Movie.title, Movie.year).where(Movie.id.in_(sample_ids))
            )).all()
            films = [f"{title} ({year or '?'})" for title, year in rows]
            out.append({
                "id": f"u{user_id}-c{c.cluster_id}",
                "description": f"User {user_id} cluster #{c.cluster_id} — existing label: {c.cluster_label!r}",
                "existing_label": c.cluster_label,
                "films": films,
                "genres": c.dominant_genres or [],
                "stats": f"{c.movie_count} films total | avg★ {c.avg_rating:.2f}" if c.avg_rating else f"{c.movie_count} films total",
            })
        return out


async def main(output: Path, delay: float, user_id: int | None):
    groq_key = os.environ.get("GROQ_API_KEY")
    if not groq_key:
        print("ERROR: GROQ_API_KEY not set")
        sys.exit(1)

    client = AsyncOpenAI(
        api_key=groq_key,
        base_url="https://api.groq.com/openai/v1",
    )

    if user_id:
        clusters = await load_real_clusters(user_id)
        logger.info(f"Loaded {len(clusters)} real clusters for user {user_id}")
    else:
        clusters = CLUSTERS
        logger.info(f"Using {len(clusters)} synthetic clusters")

    total = len(clusters) * len(MODELS)
    logger.info(f"Cluster naming A/B: {len(clusters)} clusters × {len(MODELS)} models = {total} calls")

    started = time.time()
    results: dict[str, dict] = {}
    success = 0
    for c_idx, cluster in enumerate(clusters, 1):
        logger.info(f"[{c_idx}/{len(clusters)}] {cluster['description']}")
        results[cluster["id"]] = {"cluster": cluster, "labels": {}}
        messages = build_prompt(cluster)
        for model_alias, model_id in MODELS.items():
            desc, err = await call_groq(client, model_id, messages)
            # For cluster naming, 2-4 words is EXPECTED. call_groq flags it as
            # VeryShortResponse but the text is valid — treat as success here.
            if err and err != "VeryShortResponse":
                results[cluster["id"]]["labels"][model_alias] = f"_(ERROR — {err})_"
                logger.warning(f"  {model_alias}: {err}")
            elif desc:
                label = desc.strip().rstrip(".!,;:").strip('"').strip("'")
                results[cluster["id"]]["labels"][model_alias] = label
                success += 1
                logger.info(f"  {model_alias}: '{label}' ({len(label.split())} words)")
            else:
                results[cluster["id"]]["labels"][model_alias] = "_(empty)_"
                logger.warning(f"  {model_alias}: empty result")
            await asyncio.sleep(delay)

    elapsed = time.time() - started
    logger.info(f"Done in {elapsed:.0f}s. {success}/{total} successful.")

    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as fp:
        fp.write("# Cluster naming A/B — Scout vs 70B\n\n")
        fp.write(f"- Source: {'real user_id=' + str(user_id) if user_id else 'synthetic clusters'}\n")
        fp.write(f"- Clusters: **{len(clusters)}**\n")
        fp.write(f"- Models: **{', '.join(MODELS.keys())}**\n")
        fp.write(f"- Successful: **{success}** / {total}\n")
        fp.write(f"- Elapsed: {elapsed:.0f}s\n\n")
        fp.write("Read each cluster's films + the labels from each model side-by-side and judge:\n"
                 "- Is the label concise (2-4 words) and accurate?\n"
                 "- Does it capture the cluster's identity (genre/mood/style/era) or default to generic?\n"
                 "- For mixed clusters, does the model honestly admit it's mixed?\n"
                 "- When real data: how does the existing production label compare to the new ones?\n\n---\n\n")
        for cluster_id, data in results.items():
            cluster = data["cluster"]
            labels = data["labels"]
            fp.write(f"## {cluster['id']} — {cluster['description']}\n\n")
            fp.write("**Films:**\n")
            for f in cluster["films"]:
                fp.write(f"- {f}\n")
            fp.write(f"\n**Genres:** {', '.join(cluster['genres'])}\n\n")
            fp.write(f"**Stats:** {cluster['stats']}\n\n")
            if cluster.get("existing_label"):
                fp.write(f"- **Existing production label:** `{cluster['existing_label']}`\n")
            for model_alias in MODELS.keys():
                label = labels.get(model_alias, "_(no result)_")
                fp.write(f"- **`{model_alias}`:** {label}\n")
            fp.write("\n---\n\n")

    logger.info(f"Report written to {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("experiment_cluster_naming_results.md"))
    parser.add_argument("--delay", type=float, default=0.5)
    parser.add_argument("--user-id", type=int, default=None,
                        help="If set, load real clusters of this user (sample_movie_ids) instead of the synthetic panel.")
    args = parser.parse_args()

    asyncio.run(main(output=args.output, delay=args.delay, user_id=args.user_id))
