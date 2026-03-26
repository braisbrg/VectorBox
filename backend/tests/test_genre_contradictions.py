from services.nlp_search import MovieSearchIntent, apply_genre_contradictions

def test_horror_excludes_comedy():
    intent = MovieSearchIntent(
        semantic_query="scary ghost film",
        include_genres=["Horror"],
        exclude_genres=[],
        reasoning="Test"
    )
    result = apply_genre_contradictions(intent)
    assert "Comedy" in result.exclude_genres
    assert "Animation" in result.exclude_genres
    assert "Horror" not in result.exclude_genres  # never exclude what user wants

def test_explicit_exclusion_preserved():
    intent = MovieSearchIntent(
        semantic_query="horror film not funny",
        include_genres=["Horror"],
        exclude_genres=["Romance"],  # LLM set this explicitly
        reasoning="Test"
    )
    result = apply_genre_contradictions(intent)
    assert "Romance" in result.exclude_genres  # preserved
    assert "Comedy" in result.exclude_genres   # added by contradictions

def test_no_contradictions_for_drama():
    intent = MovieSearchIntent(
        semantic_query="drama film",
        include_genres=["Drama"],
        exclude_genres=[],
        reasoning="Test"
    )
    result = apply_genre_contradictions(intent)
    assert result.exclude_genres == []  # Drama has no contradictions
