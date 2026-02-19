import asyncio
import sys
import os
import json
from typing import Dict, Any, Optional

# Add parent directory to path so we can import services
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.nlp_search import parse_user_intent, MovieSearchIntent

# Mocking Qdrant libraries just for filter construction display
# We don't need actual Qdrant client, just the structure logic
class FieldCondition:
    def __init__(self, key, range=None, match=None):
        self.key = key
        self.range = range
        self.match = match
    def to_dict(self):
        d = {"key": self.key}
        if self.range: d["range"] = self.range
        if self.match: d["match"] = self.match
        return d

class MatchAny:
    def __init__(self, any):
        self.any = any
    def to_dict(self):
        return {"any": self.any}

class MatchValue:
    def __init__(self, value):
        self.value = value
    def to_dict(self):
        return {"value": self.value}
        
class Filter:
    def __init__(self, must=None, must_not=None):
        self.must = must
        self.must_not = must_not
    def to_dict(self):
        d = {}
        if self.must: d["must"] = [x.to_dict() if hasattr(x, "to_dict") else x for x in self.must]
        if self.must_not: d["must_not"] = [x.to_dict() if hasattr(x, "to_dict") else x for x in self.must_not]
        return d

def construct_qdrant_filter_simulation(filters: Dict[str, Any]) -> Dict:
    """
    Simulates the filter construction logic from QdrantService
    to show what would be sent to the DB.
    """
    must_conditions = []
    must_not_conditions = []
    
    # 1. Year range filters
    if "year_min" in filters and filters["year_min"]:
        must_conditions.append(FieldCondition(key="year", range={"gte": filters["year_min"]}))
    if "year_max" in filters and filters["year_max"]:
        must_conditions.append(FieldCondition(key="year", range={"lte": filters["year_max"]}))

    # 2. Genre filters
    if "include_genres" in filters and filters["include_genres"]:
        must_conditions.append(FieldCondition(key="genres", match=MatchAny(any=filters["include_genres"])))
    
    if "exclude_genres" in filters and filters["exclude_genres"]:
        # Logic for exclusion is typically complex in Qdrant (must_not match any)
        # Detailed logic: must_not -> FieldCondition(genres, match=MatchAny(exclude_genres))
        must_not_conditions.append(FieldCondition(key="genres", match=MatchAny(any=filters["exclude_genres"])))

    # 3. Runtime filters
    if "max_runtime_minutes" in filters and filters["max_runtime_minutes"]:
        must_conditions.append(FieldCondition(key="runtime", range={"lte": filters["max_runtime_minutes"]}))
    
    # 4. Popularity Vibe
    if "popularity_vibe" in filters:
        vibe = filters["popularity_vibe"]
        if vibe == "hidden_gem":
            must_conditions.append(FieldCondition(key="vote_count", range={"lt": 5000}))
            must_conditions.append(FieldCondition(key="vote_average", range={"gte": 7.0}))
        elif vibe == "blockbuster":
            must_conditions.append(FieldCondition(key="vote_count", range={"gte": 10000}))

    # 5. Language
    if "original_language" in filters and filters["original_language"]:
        must_conditions.append(FieldCondition(key="original_language", match=MatchValue(value=filters["original_language"])))

    # Build final filter object
    qdrant_filter = Filter(must=must_conditions if must_conditions else None, 
                            must_not=must_not_conditions if must_not_conditions else None)
    return qdrant_filter.to_dict()


async def run_stress_test():
    print("=== MAGIC BOX STRESS TEST ===")
    print("==================================================")
    
    test_cases = [
        "Cyberpunk movies from the 80s",
        "Comedy but not romantic",
        "Something extremely sad and depressing hidden gem",
        "Película de terror psicológico española",
        "A movie about a dream within a dream"
    ]
    
    for i, query in enumerate(test_cases, 1):
        print(f"\n[TEST CASE #{i}: '{query}']")
        print("-" * 50)
        
        try:
            # 1. Intent Analysis
            # print("Analyzing with Llama 3.3...")
            intent: MovieSearchIntent = await parse_user_intent(query)
            
            # Print Raw Intent
            print("\nGENERATED INTENT (JSON):")
            intent_dict = intent.model_dump()
            print(json.dumps(intent_dict, indent=2, ensure_ascii=False))
            
            # 2. Construct Filter
            print("\nQDRANT FILTER (Simulation):")
            qdrant_filter = construct_qdrant_filter_simulation(intent_dict)
            
            # Custom plain print for readability
            print(json.dumps(qdrant_filter, indent=2, default=lambda o: o.__dict__))
            
            # 3. Semantic Query
            print(f"SEMANTIC EXPANSION:\n'{intent.semantic_query}'")
            print(f"REASONING:\n{intent.reasoning}")
            
        except Exception as e:
            print(f"ERROR: {e}")
            import traceback
            traceback.print_exc()

        print("=" * 50)

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run_stress_test())
