import pytest

# Required for pytest-asyncio
pytest_plugins = ('pytest_asyncio',)

from services.omdb_client import OMDbClient

def test_vectorbox_score_normalization():
    """Test FiveThirtyEight-style de-inflation normalization."""
    client = OMDbClient.__new__(OMDbClient)  # Skip __init__ to avoid API key check
    
    # Mock OMDb data: IMDb 7.0, RT 85%, Meta 65
    omdb_data = {
        "imdbRating": "7.0",
        "Ratings": [{"Source": "Rotten Tomatoes", "Value": "85%"}],
        "Metascore": "65"
    }
    tmdb_vote = 7.2  # TMDB rating
    
    result = client.calculate_vectorbox_score(omdb_data, tmdb_vote)
    
    # Verify normalization logic:
    # IMDb 7.0 -> (7.0 - 5) * 20 = 40
    # TMDB 7.2 -> (7.2 - 5) * 20 = 44
    # RT 85 -> 85 (raw)
    # Meta 65 -> 65 (raw)
    # Average: (40 + 44 + 85 + 65) / 4 = 58.5
    
    assert result["score"] is not None
    assert abs(result["score"] - 58.5) < 0.1, f"Expected ~58.5, got {result['score']}"
    
    # Verify breakdown returns original scales
    assert result["breakdown"]["imdb"] == 7.0
    assert result["breakdown"]["tmdb"] == 7.2
    assert result["breakdown"]["rt"] == 85
    assert result["breakdown"]["meta"] == 65

def test_vectorbox_score_missing_sources():
    """Test weight redistribution when sources are missing."""
    client = OMDbClient.__new__(OMDbClient)
    
    # Only IMDb available
    omdb_data = {
        "imdbRating": "8.0",
        "Ratings": [],
        "Metascore": "N/A"
    }
    
    result = client.calculate_vectorbox_score(omdb_data, None)  # No TMDB either
    
    # IMDb 8.0 -> (8.0 - 5) * 20 = 60
    # Only source, so full weight
    assert result["score"] == 60.0

def test_vectorbox_score_low_imdb():
    """Test de-inflation floors at 0 for scores <= 5."""
    client = OMDbClient.__new__(OMDbClient)
    
    omdb_data = {
        "imdbRating": "4.5",  # Below 5.0 threshold
        "Ratings": [],
        "Metascore": "N/A"
    }
    
    result = client.calculate_vectorbox_score(omdb_data, 4.0)  # TMDB also low
    
    # IMDb 4.5 -> max(0, (4.5 - 5) * 20) = max(0, -10) = 0
    # TMDB 4.0 -> max(0, (4.0 - 5) * 20) = max(0, -20) = 0
    assert result["score"] == 0.0
