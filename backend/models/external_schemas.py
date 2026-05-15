from pydantic import BaseModel, Field
from typing import List, Optional, Any

# --- OMDb Schemas ---

class OMDbRating(BaseModel):
    Source: str
    Value: str

class OMDbResponse(BaseModel):
    Response: str
    Error: Optional[str] = None
    Title: Optional[str] = None
    Year: Optional[str] = None
    imdbRating: Optional[str] = None
    imdbVotes: Optional[str] = None  # e.g. "1,234,567" — parsed to int by callers
    Ratings: List[OMDbRating] = Field(default_factory=list)
    Metascore: Optional[str] = None
    Rated: Optional[str] = None       # MPAA: G/PG/PG-13/R/NC-17/NR/TV-14
    Awards: Optional[str] = None      # raw "Won 3 Oscars. 33 wins & 41 nominations total"
    Country: Optional[str] = None     # comma-separated countries
    Language: Optional[str] = None    # comma-separated languages

    class Config:
        extra = "ignore"

class VectorBoxBreakdown(BaseModel):
    imdb: Optional[float] = None
    meta: Optional[int] = None
    tmdb: Optional[float] = None

class VectorBoxScore(BaseModel):
    score: Optional[float] = None
    breakdown: VectorBoxBreakdown = Field(default_factory=VectorBoxBreakdown)

# --- Qdrant Schemas ---

class QdrantPayload(BaseModel):
    tmdb_id: int
    title: str
    year: Optional[int] = None
    genres: List[str] = Field(default_factory=list)
    overview: Optional[str] = None
    poster_path: Optional[str] = None
    rating: Optional[float] = None  # This maps to vote_average
    vote_count: Optional[int] = None
    runtime: Optional[int] = None
    original_language: Optional[str] = None
    keywords: List[str] = Field(default_factory=list)
    
    # Extended Metrics
    vectorbox_score: Optional[float] = None
    imdb_rating: Optional[float] = None
    metacritic_rating: Optional[int] = None
    
    # Spanish Metadata
    title_es: Optional[str] = None
    overview_es: Optional[str] = None
    
    # Credits
    directors: List[str] = Field(default_factory=list)
    cast: List[str] = Field(default_factory=list)

    class Config:
        extra = "ignore"
