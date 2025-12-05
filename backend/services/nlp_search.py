import os
import instructor
from pydantic import BaseModel, Field
from typing import List, Optional, Literal
from openai import OpenAI
import logging

logger = logging.getLogger(__name__)

# STEP 1: Rich Data Model with Expert Guidance
class MovieSearchIntent(BaseModel):
    """Advanced search intent with semantic expansion and nuanced interpretation"""
    
    # 1. Semantic Expansion: LLM expands keywords with synonyms
    semantic_query: str = Field(
        ..., 
        description="A rich, descriptive version of the user's request for vector search. You MUST expand keywords with synonyms and related themes. Example: Input 'gangsters' -> Output 'organized crime, mafia, mob, crime drama, violence, noir'."
    )

    # 2. Time Interpretation: Translates vague time references to years
    year_min: Optional[int] = Field(
        None, 
        description="Start year. Interpret '80s' as 1980, 'Modern' as 2010, 'Recent' as 2020. Leave None if not specified."
    )
    year_max: Optional[int] = Field(
        None, 
        description="End year. Interpret 'Old/Classic' as 1985, '90s' as 1999. Leave None if not specified."
    )

    # 3. Content Filters
    include_genres: Optional[List[str]] = Field(
        None, 
        description="Official TMDB genres to include (e.g., Horror, Comedy, Action). Map user slang like 'scary' to 'Horror', 'funny' to 'Comedy'."
    )
    exclude_genres: Optional[List[str]] = Field(
        None, 
        description="Genres to strictly exclude (e.g., 'no cartoons' -> exclude Animation, 'no kids stuff' -> exclude Family)."
    )
    
    # 4. Technical Constraints
    max_runtime_minutes: Optional[int] = Field(
        None, 
        description="Max duration in minutes. Interpret 'short' as 90, 'quick' as 80, 'brief' as 75."
    )
    
    # 5. The "Vibe" Filter - NEW!
    popularity_vibe: Literal["blockbuster", "hidden_gem", "any"] = Field(
        "any", 
        description="Select 'hidden_gem' if user asks for 'underground/underrated/unknown/deep cuts/obscure'. Select 'blockbuster' for 'famous/popular/hits/well-known'. Default 'any'."
    )
    
    # 6. Language Filter - NEW!
    original_language: Optional[str] = Field(
        None,
        description="ISO 639-1 language code (e.g., 'fr', 'es', 'ko', 'ja'). Use ONLY if user explicitly asks for a language (e.g., 'French movies')."
    )

    # 7. Reference Movie - NEW!
    reference_movie: Optional[str] = Field(
        None,
        description="If the user asks for movies 'like', 'similar to', or 'resembling' a specific movie, extract that movie's title here. Example: 'films like Inception' -> 'Inception'."
    )

    # 8. Reasoning for transparency
    reasoning: str = Field(
        ..., 
        description="Briefly explain (1-2 sentences) the logic used to derive these filters from the user's query."
    )

# 2. Initialize Groq Client with Llama 4
# Note: We use the standard OpenAI client pointing to Groq's URL
def get_groq_client():
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        logger.warning("GROQ_API_KEY not found. NLP search will fail or fallback.")
        return None
        
    client = OpenAI(
        base_url="https://api.groq.com/openai/v1",
        api_key=api_key,
    )
    return instructor.patch(client, mode=instructor.Mode.TOOLS)

def get_fallback_client():
    # Fallback to OpenAI if configured, or just fail gracefully
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
        
    client = OpenAI(api_key=api_key)
    return instructor.patch(client, mode=instructor.Mode.TOOLS)

async def parse_user_intent(user_query: str) -> MovieSearchIntent:
    """
    Uses Llama 3.3 Scout to parse natural language into rich database filters.
    Handles vague, complex queries with semantic expansion.
    """
    client = get_groq_client()
    
    if not client:
        # Simple fallback if no API key: treat entire query as semantic search
        logger.info("No Groq API key, using simple fallback")
        return MovieSearchIntent(
            semantic_query=user_query,
            reasoning="No LLM available, using raw query"
        )

    # STEP 2: Expert System Prompt
    system_prompt = """You are an expert film archivist and recommendation engine. 
Your goal is to translate raw, messy natural language into precise structured database filters.

RULES FOR INTERPRETATION:
1. **Semantic Expansion (CRITICAL):**
   - Never just return the user's exact words in `semantic_query`.
   - Always expand with at least 3-4 synonyms or related sub-genres.
   - Example: "Space wars" -> "space opera, galactic war, sci-fi battles, starships, futuristic conflict"
   - Example: "gangsters" -> "organized crime, mafia, mob, crime drama, gangster film, noir"

2. **Time Periods:** 
   - "Old" / "Classic" -> Set `year_max` to 1985 (unless context suggests Silent Era).
   - "Modern" / "Recent" -> Set `year_min` to 2015.
   - "New" / "Latest" -> Set `year_min` to 2020.
   - "90s" -> year_min: 1990, year_max: 1999.
   - "80s" -> year_min: 1980, year_max: 1989.

3. **Genre Mapping (use EXACT TMDB names):**
   - Available: Action, Adventure, Animation, Comedy, Crime, Documentary, Drama, Family, Fantasy, History, Horror, Music, Mystery, Romance, Science Fiction, Thriller, War, Western
   - Map slang: "scary" -> Horror, "funny" -> Comedy, "sci-fi" -> Science Fiction
   - Expand implicit genres: "gangster" -> Crime, "superhero" -> Action + Fantasy

4. **Mood & Popularity:**
   - "Hidden gems" / "Deep cuts" / "Underrated" / "Unknown" -> Set `popularity_vibe` to 'hidden_gem'.
   - "Popular" / "Famous" / "Blockbuster" / "Hit" -> Set `popularity_vibe` to 'blockbuster'.
   - Default: 'any'

5. **Language (ISO 639-1 Code):**
   - "French" -> 'fr'
   - "Korean" -> 'ko'
   - "Japanese" / "Anime" (if context implies) -> 'ja'
   - "Spanish" -> 'es'
   - "German" -> 'de'
   - "Italian" -> 'it'
   - "Cantonese" / "Hong Kong" -> 'zh'

6. **Runtime:**
   - "Short" -> 90 minutes
   - "Quick" / "Brief" -> 80 minutes
   - Specific times: "under 2 hours" -> 120 minutes

7. **Reference Movies:**
   - If the user asks for movies "like", "similar to", or "resembling" a specific film, extract the title into `reference_movie`.
   - Example: "movies like Inception" -> reference_movie: "Inception"
   - Example: "something similar to The Godfather" -> reference_movie: "The Godfather"
   - Example: "Inception" -> reference_movie: None (unless user explicitly asks for similar)

8. **Safety:**
   - If a request is contradictory, prioritize the semantic query over strict filters.
   - Always provide reasoning to explain your interpretation.
"""

    try:
        return client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            response_model=MovieSearchIntent,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_query},
            ],
            temperature=0.1,
        )
    except Exception as e:
        # Check for rate limit or connection error (generic catch for robustness)
        error_msg = str(e).lower()
        if "rate limit" in error_msg or "connection" in error_msg or "429" in error_msg:
             logger.warning(f"Primary model rate limited or failed ({e}). Switching to fallback model.")
             try:
                return client.chat.completions.create(
                    model="llama-3.1-8b-instant", # Fast fallback model
                    response_model=MovieSearchIntent,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_query},
                    ],
                    temperature=0.1,
                )
             except Exception as e_fallback:
                 logger.error(f"Groq fallback model failed: {e_fallback}")
                 raise e # Re-raise to trigger OpenAI fallback
        
        logger.error(f"Groq primary model failed with non-retryable error: {e}")
        raise e # Re-raise to trigger OpenAI fallback
        
        # Fallback Strategy
        fallback_client = get_fallback_client()
        if fallback_client:
            try:
                return fallback_client.chat.completions.create(
                    model="gpt-4o-mini", # Using 4o-mini as efficient fallback
                    response_model=MovieSearchIntent,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_query},
                    ],
                    temperature=0.1,
                )
            except Exception as e2:
                logger.error(f"Fallback failed: {e2}")
        
        # Ultimate fallback: just return the query
        return MovieSearchIntent(
            semantic_query=user_query,
            reasoning="LLM failed, using raw query as fallback"
        )
