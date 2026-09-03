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
    # L-1: user_id removed — always derived from JWT token server-side
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


class FilteredSearchRequest(BaseModel):
    """Rail EXECUTE_QUERY: filter-first, taste-ranked. Hard constraints define the
    pool (whole catalogue), the user's taste centroid ranks within it."""
    year_min: Optional[conint(ge=1800, le=2100)] = None
    year_max: Optional[conint(ge=1800, le=2100)] = None
    max_runtime: Optional[conint(ge=1, le=1000)] = None
    min_score: Optional[confloat(ge=0, le=100)] = None  # VBS quality floor Q 0-100
    genres: Optional[List[constr(max_length=50)]] = None
    providers: Optional[List[int]] = None  # TMDB provider IDs to require (match is robust vs names)
    country_code: constr(min_length=2, max_length=2) = "ES"
    # Mood: nombre de cuadrante, no rangos sueltos. El cliente no debería poder
    # pedir combinaciones que no hemos mirado — validado contra QUADRANTS.
    mood: Optional[constr(max_length=20)] = None
    # Fuente, no filtro: acota el universo del que salen las filas. Viaja aquí porque
    # el conmutador vive en el mismo panel que los sliders y el ánimo, y sin él una
    # consulta con ánimo perdería la lista en silencio.
    watchlist: bool = False


class RecommendationResponse(BaseModel):
    """Movie recommendation with similarity score"""
    movie: MovieMetadata
    similarity_score: confloat(ge=0, le=100)
    streaming_available: bool
    streaming_providers: List[str] = []
    contributors: List[Dict] = [] # For "Why Recommended"


class UserResponse(BaseModel):
    """User profile response"""
    id: int
    username: str
    country_code: str
    created_at: datetime
    has_data: bool = False
    letterboxd_username: Optional[str] = None
    include_shorts: bool = False

    class Config:
        from_attributes = True


class UserPreferencesRequest(BaseModel):
    """Ajustes de descubrimiento. Todo opcional: sólo se aplica lo que llega."""
    include_shorts: Optional[bool] = None


class TokenResponse(BaseModel):
    """Auth token response"""
    token: str
    user_id: int
    username: str
    has_data: bool = False
    letterboxd_username: Optional[str] = None


class TaskStatusResponse(BaseModel):
    """Background task status"""
    task_id: str
    status: str  # "pending", "processing", "completed", "failed"
    progress: int = 0  # 0-100
    step: Optional[str] = None  # Current step description


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


class FeedItem(BaseModel):
    """Individual item in a feed section"""
    id: int  # Movie TMDB ID
    title: str
    poster_url: Optional[str] = None
    # Parecido en escala 60–99. HOY SIEMPRE None, y eso está medido (2026-08-10):
    # ninguno de los 13 productores del feed puede rellenarlo honestamente.
    #
    #   11 pasaban una constante (1.0/0.95/0.9/0.85) que la escala convertía en 99
    #      → 459 de 459 items servidos valían exactamente 99.0
    #    1 pasaba `vectorbox_score/100`, o sea CALIDAD por un normalizador de
    #      SIMILITUD
    #    2 (BYW, hidden gems) pasaban un coseno ya alterado: BYW lo multiplica por
    #      0.3/0.6 como penalización anti-vector, y hidden gems entrega
    #      `calidad*0.7 + similitud*0.3` más un boost de exotismo. Las dos
    #      operaciones son correctas para ORDENAR, que es para lo que existen,
    #      pero el resultado ya no está en la escala del coseno: medido, BYW se
    #      hundía a 60–61 y hidden gems saturaba 10/10 a 99.
    #
    # Para "Popular esta semana" o "En tu radar" el concepto además no aplica: no
    # hay contra qué parecerse. None es el idioma que ya usa el producto para esto
    # (la rama de catálogo de la Magic Box y `warm_showcase` tratan `score is None`
    # aparte).
    #
    # Rellenarlo exige un productor que pase un coseno SIN alterar. Mientras no
    # exista, el campo se queda como contrato y no como dato — y si nunca aparece,
    # bórralo: nada del frontend lo pinta.
    match_score: Optional[confloat(ge=0, le=100)] = None
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
    release_dates: Optional[Dict[str, str]] = None
    title_es: Optional[str] = None
    overview_es: Optional[str] = None
    letterboxd_rating: Optional[float] = None
    backdrop_url: Optional[str] = None  # wide art for the feed hero


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


class LinkLetterboxdRequest(BaseModel):
    """L-3: Request body for linking a Letterboxd username"""
    letterboxd_username: constr(min_length=1, max_length=40, pattern=r'^[a-zA-Z0-9_-]+$')

