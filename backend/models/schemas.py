"""
Pydantic schemas for request/response validation with security constraints
"""
from pydantic import BaseModel, Field, validator, constr, conint, confloat
from typing import Optional, List, Dict
from datetime import datetime
from enum import Enum


# Security: Strict validation for user inputs

# System Response Schemas (for OpenAPI documentation)
class HealthResponse(BaseModel):
    """Health check endpoint response"""
    status: str
    service: str


class RootResponse(BaseModel):
    """API root endpoint response"""
    message: str
    version: str
    docs: str


class MovieMetadata(BaseModel):
    """TMDB movie metadata"""
    tmdb_id: int
    title: constr(max_length=500)
    original_title: Optional[constr(max_length=500)] = None
    year: Optional[conint(ge=1800, le=2100)] = None
    runtime: Optional[conint(ge=0, le=1000)] = None
    genres: List[str] = []
    overview: Optional[str] = None
    poster_path: Optional[str] = None
    backdrop_path: Optional[str] = None
    vote_average: Optional[confloat(ge=0, le=10)] = None
    
    # Phase 12 Fields
    vectorbox_score: Optional[float] = None
    imdb_rating: Optional[float] = None
    metacritic_rating: Optional[int] = None
    rotten_tomatoes_rating: Optional[int] = None
    release_dates: Optional[Dict[str, str]] = None
    title_es: Optional[str] = None
    overview_es: Optional[str] = None


class CSVUploadResponse(BaseModel):
    """Response for CSV upload"""
    status: str
    message: str
    movies_processed: int
    movies_enriched: int
    errors: List[str] = []
    task_id: Optional[str] = None


class ClusterInfo(BaseModel):
    """User taste cluster information"""
    cluster_id: int
    label: str
    movie_count: int
    avg_rating: float
    dominant_genres: List[str]
    sample_movies: List[MovieMetadata]


class RecommendationRequest(BaseModel):
    """Request for movie recommendations with filters"""
    user_id: int
    cluster_id: Optional[int] = None  # Mood selection
    year_min: Optional[conint(ge=1800, le=2100)] = None
    year_max: Optional[conint(ge=1800, le=2100)] = None
    genres: Optional[List[constr(max_length=50)]] = []
    runtime_min: Optional[conint(ge=1, le=1000)] = None
    runtime_max: Optional[conint(ge=1, le=1000)] = None
    streaming_providers: Optional[List[int]] = []  # TMDB provider IDs
    country_code: constr(min_length=2, max_length=2) = "ES"  # ISO country code for streaming
    limit: conint(ge=1, le=100) = 20  # Security: Limit results
    
    # New Advanced Filters
    min_vote_count: Optional[int] = None
    min_rating: Optional[confloat(ge=0, le=100)] = None
    original_language: Optional[constr(min_length=2, max_length=10)] = None
    include_keywords: Optional[List[str]] = []
    watchlist_only: Optional[bool] = False
    include_low_quality: Optional[bool] = False # Trash Gate Bypass
    page: conint(ge=1) = 1 # Pagination
    
    @validator('country_code')
    def validate_country_code(cls, v):
        """Ensure country code is uppercase"""
        return v.upper() if v else "ES"
    
    @validator('year_max')
    def validate_year_range(cls, v, values):
        """Ensure year_max >= year_min"""
        if v and 'year_min' in values and values['year_min']:
            if v < values['year_min']:
                raise ValueError('year_max must be >= year_min')
        return v
    
    @validator('runtime_max')
    def validate_runtime_range(cls, v, values):
        """Ensure runtime_max >= runtime_min"""
        if v and 'runtime_min' in values and values['runtime_min']:
            if v < values['runtime_min']:
                raise ValueError('runtime_max must be >= runtime_min')
        return v


class RecommendationResponse(BaseModel):
    """Movie recommendation with similarity score"""
    movie: MovieMetadata
    similarity_score: confloat(ge=0, le=100)
    streaming_available: bool
    streaming_providers: List[str] = []
    contributors: List[Dict] = [] # For "Why Recommended"


class UserCreate(BaseModel):
    """Create new user"""
    username: constr(min_length=3, max_length=20, pattern=r'^[a-zA-Z0-9_-]+$', strip_whitespace=True)
    email: Optional[constr(max_length=255, strip_whitespace=True)] = None
    country_code: constr(min_length=2, max_length=2, strip_whitespace=True) = "ES"


class UserResponse(BaseModel):
    """User profile response"""
    id: int
    username: str
    country_code: str
    created_at: datetime
    has_data: bool = False
    letterboxd_username: Optional[str] = None
    
    class Config:
        from_attributes = True


# v1.1: Auth Schemas
class RegisterRequest(BaseModel):
    """Register a new VectorBox user"""
    username: constr(min_length=3, max_length=20, pattern=r'^[a-zA-Z0-9_-]+$', strip_whitespace=True)
    pin: constr(min_length=4, max_length=4, pattern=r'^\d{4}$', strip_whitespace=True)
    country_code: constr(min_length=2, max_length=2, strip_whitespace=True) = "ES"


class LoginRequest(BaseModel):
    """Login with username and PIN"""
    username: constr(min_length=3, max_length=20, pattern=r'^[a-zA-Z0-9_-]+$', strip_whitespace=True)
    pin: constr(min_length=4, max_length=4, pattern=r'^\d{4}$', strip_whitespace=True)


class TokenResponse(BaseModel):
    """Auth token response"""
    token: str
    user_id: int
    username: str
    has_data: bool = False
    letterboxd_username: Optional[str] = None


class LinkLetterboxdRequest(BaseModel):
    """Link a Letterboxd profile to user account"""
    letterboxd_username: constr(min_length=1, max_length=50, pattern=r'^[a-zA-Z0-9_-]+$')


class TaskStatusResponse(BaseModel):
    """Background task status"""
    task_id: str
    status: str  # "pending", "processing", "completed", "failed"
    progress: int = 0  # 0-100
    step: Optional[str] = None  # Current step description


class StreamingProviderCreate(BaseModel):
    """Add streaming provider to user profile"""
    provider_id: int
    provider_name: constr(max_length=100)
    country_code: constr(min_length=2, max_length=2)


class CompatibilityRequest(BaseModel):
    """Request for user compatibility calculation"""
    user_id_1: int
    user_id_2: int


class CompatibilityResponse(BaseModel):
    """User compatibility score"""
    user_1: str
    user_2: str
    similarity_score: confloat(ge=0, le=1)
    shared_movies: int
    shared_genres: List[str]


class GroupWatchlistRequest(BaseModel):
    """Request for group watchlist intersection"""
    user_ids: List[int] = Field(..., min_items=2, max_items=10)  # Security: Limit group size
    min_avg_rating: Optional[confloat(ge=0, le=5)] = None


class GroupRecommendationRequest(BaseModel):
    """Request for group recommendations"""
    user_ids: List[int] = Field(..., min_items=2, max_items=10)
    year_min: Optional[conint(ge=1800, le=2100)] = None
    year_max: Optional[conint(ge=1800, le=2100)] = None
    genres: Optional[List[constr(max_length=50)]] = []
    runtime_min: Optional[conint(ge=1, le=1000)] = None
    runtime_max: Optional[conint(ge=1, le=1000)] = None
    country_code: constr(min_length=2, max_length=2) = "ES"
    limit: conint(ge=1, le=100) = 20
    
    @validator('country_code')
    def validate_country_code(cls, v):
        """Ensure country code is uppercase"""
        return v.upper() if v else "ES"


class ErrorResponse(BaseModel):
    """Standard error response"""
    detail: str
    error_code: Optional[str] = None


class MovieCardSchema(BaseModel):
    """Lightweight schema for list views to prevent over-fetching"""
    id: int
    title: str
    poster_url: Optional[str] = None
    match_score: confloat(ge=0, le=100)
    year: Optional[int] = None
    streaming_providers: List[str] = []
    vectorbox_score: Optional[float] = None
    
    # Minimal fields for UI badges
    imdb_rating: Optional[float] = None
    rotten_tomatoes_rating: Optional[int] = None
    release_dates: Optional[Dict[str, str]] = None


class FeedItem(BaseModel):
    """Individual item in a feed section"""
    id: int  # Movie TMDB ID
    title: str
    poster_url: Optional[str] = None
    match_score: confloat(ge=0, le=100)  # Percentage match
    streaming_providers: List[str] = []
    year: Optional[int] = None
    runtime: Optional[int] = None
    letterboxd_uri: Optional[str] = None
    rating: Optional[float] = None  # TMDB Vote Average or User Rating
    overview: Optional[str] = None  # Synopsis
    contributors: List[dict] = []  # For item-based explanations
    
    # Phase 12 Fields
    vectorbox_score: Optional[float] = None
    imdb_rating: Optional[float] = None
    metacritic_rating: Optional[int] = None
    rotten_tomatoes_rating: Optional[int] = None
    release_dates: Optional[Dict[str, str]] = None
    title_es: Optional[str] = None
    overview_es: Optional[str] = None
    letterboxd_rating: Optional[float] = None


class FeedSection(BaseModel):
    """A horizontal row in the feed"""
    id: str  # Unique section identifier (e.g., "because_you_watched")
    title: str  # Display title (e.g., "Because you watched Interstellar")
    type: str = "horizontal_list"  # Always horizontal for now
    items: List[FeedItem]


class FeedResponse(BaseModel):
    """Complete feed response with multiple sections"""
    feed: List[FeedSection]
    status: str = "ok"  # "ok", "incomplete", "error"

