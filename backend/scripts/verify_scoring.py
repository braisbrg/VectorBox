#!/usr/bin/env python
"""Verification script for VectorBox Score normalization."""
import sys
sys.path.insert(0, '/app')

from services.omdb_client import OMDbClient

def main():
    # Create client without API key check
    client = OMDbClient.__new__(OMDbClient)
    
    # Test 1: Full data (IMDb 7.0, RT 85%, Meta 65, TMDB 7.2)
    omdb_data = {
        "imdbRating": "7.0",
        "Ratings": [{"Source": "Rotten Tomatoes", "Value": "85%"}],
        "Metascore": "65"
    }
    result = client.calculate_vectorbox_score(omdb_data, 7.2)
    
    print("=" * 50)
    print("VectorBox Score Normalization Verification")
    print("=" * 50)
    print(f"\nTest 1: Full Data")
    print(f"  IMDb 7.0 -> normalized: {max(0, (7.0 - 5) * 20)} (expected: 40)")
    print(f"  TMDB 7.2 -> normalized: {max(0, (7.2 - 5) * 20)} (expected: 44)")  
    print(f"  RT 85%   -> raw: 85")
    print(f"  Meta 65  -> raw: 65")
    print(f"  Expected Average: (40 + 44 + 85 + 65) / 4 = 58.5")
    print(f"  Actual Score: {result['score']}")
    print(f"  Breakdown: {result['breakdown']}")
    
    assert abs(result['score'] - 58.5) < 0.1, f"FAILED: Expected ~58.5, got {result['score']}"
    print("  ✅ PASSED")
    
    # Test 2: Low IMDb (floor at 0)
    omdb_low = {"imdbRating": "4.5", "Ratings": [], "Metascore": "N/A"}
    result2 = client.calculate_vectorbox_score(omdb_low, 4.0)
    
    print(f"\nTest 2: Low Scores (Floor at 0)")
    print(f"  IMDb 4.5 -> max(0, (4.5-5)*20) = 0")
    print(f"  TMDB 4.0 -> max(0, (4.0-5)*20) = 0")
    print(f"  Actual Score: {result2['score']}")
    
    assert result2['score'] == 0.0, f"FAILED: Expected 0.0, got {result2['score']}"
    print("  ✅ PASSED")
    
    print("\n" + "=" * 50)
    print("All tests PASSED! ✅")
    print("=" * 50)

if __name__ == "__main__":
    main()
