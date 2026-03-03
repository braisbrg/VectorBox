import asyncio
import sys
import os

# Add the backend directory to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch, AsyncMock
from services.nlp_search import parse_user_intent, MovieSearchIntent
import pytest

async def run_test():
    with patch('services.nlp_search.get_scout_client') as mock_scout:
        mock_client = AsyncMock()
        
        success_response = MovieSearchIntent(
            semantic_query="gangster movies, mafia",
            reasoning="Mocked Tier 3 success"
        )
        
        mock_client.chat.completions.create.side_effect = [
            Exception("503 Service Unavailable"),
            Exception("Timeout"),
            success_response
        ]
        
        mock_scout.return_value = mock_client
        
        try:
            result = await parse_user_intent("gangster movies")
            
            if result.reasoning == "Mocked Tier 3 success":
                print("✅ FALLBACK CHAIN SUCCESS")
            else:
                print(f"❌ FALLBACK CHAIN FAILED: Unexpected reasoning: {result.reasoning}")
                sys.exit(1)
        except Exception as e:
            import traceback
            print(f"❌ FALLBACK CHAIN FAILED: {e}")
            traceback.print_exc()
            sys.exit(1)

if __name__ == "__main__":
    asyncio.run(run_test())
