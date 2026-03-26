# Genre contradiction map.
# When a user searches for key genre, automatically exclude value genres from results.
# Bidirectional where marked — e.g. Horror excludes Comedy AND Comedy excludes Horror.
GENRE_CONTRADICTIONS: dict[str, list[str]] = {
    "Horror":      ["Comedy", "Animation", "Family", "Music"],
    "Comedy":      ["Horror"],
    "Romance":     ["Horror", "War"],
    "Animation":   ["Horror", "Thriller", "Crime"],
    "Family":      ["Horror", "Thriller", "Crime", "War"],
    "Documentary": ["Animation", "Fantasy", "Horror"],
    "War":         ["Comedy", "Animation", "Family", "Romance"],
    "Musical":     ["Horror", "War", "Crime"],
}
