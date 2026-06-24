"""Shared reference-text recipe for the embedding-quality cosine check.

Name-free on purpose. The stored Qdrant vector is encoded from the V2
``cinematic_description``, which deliberately excludes the film title, director,
and cast. A reference that includes those names has no counterpart in the stored
vector to match, so it only adds unmatchable noise that depresses the cosine for
legitimate films (short/generic titles like '42', 'Z', 'Network' suffer most).
Keeping the reference name-free — overview + genres + keywords, the same recipe
as the legacy fallback — aligns it with the actual embedding and sharpens the
hallucination signal: a wrong-film enrichment still embeds far from the real
plot/genre/themes. See CLAUDE.md "Embedding & vector hygiene".

Single source of truth for both the post-upload sanity check
(``routers/upload.py``) and the offline auditor (``scripts/check_embeddings.py``)
— they previously diverged, with upload.py using a name-heavy recipe that biased
its scores downward.
"""
from __future__ import annotations


def build_embedding_reference_text(movie) -> str:
    """Name-free reference text from a Movie's overview + genres + keywords."""
    parts: list[str] = []
    if movie.overview:
        parts.append(movie.overview)
    if movie.genres:
        parts.append("Genres: " + ", ".join(movie.genres))
    if movie.keywords:
        parts.append("Themes: " + ", ".join((movie.keywords or [])[:15]))
    return ". ".join(parts).strip()
