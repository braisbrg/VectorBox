import sys
import os

# Add the backend directory to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def filter_es_providers(all_providers: list[str]) -> list[str]:
    """Pure function to filter provider names against the ES whitelist."""
    es_whitelist = {"Netflix", "Amazon Prime Video", "HBO Max", "Disney+", "Apple TV", "Movistar+", "Filmin"}
    return [p for p in all_providers if p in es_whitelist]

def run_test():
    mock_providers = ["Netflix", "Hulu", "Peacock", "Movistar+"]
    
    result = filter_es_providers(mock_providers)
    
    try:
        assert "Hulu" not in result, "Hulu should not be in result"
        assert "Peacock" not in result, "Peacock should not be in result"
        assert "Netflix" in result, "Netflix should be in result"
        assert "Movistar+" in result, "Movistar+ should be in result"
        print("✅ WHITELIST FILTER SUCCESS")
    except AssertionError as e:
        print(f"❌ WHITELIST FILTER FAILED — unexpected providers: {result} - Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    run_test()
