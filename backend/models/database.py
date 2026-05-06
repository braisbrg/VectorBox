"""
SQLAlchemy database models with security considerations
"""
from sqlalchemy import Column, Integer, String, Float, DateTime, Date, ForeignKey, Text, Boolean, Index
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import ARRAY, UUID, JSONB
from datetime import datetime
import uuid

from config import Base


class User(Base):
    """User profiles"""
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    uuid = Column(UUID(as_uuid=True), default=uuid.uuid4, unique=True, nullable=False)
    username = Column(String(50), unique=True, index=True, nullable=False)
    email = Column(String(255), unique=True, index=True)  # For future auth
    created_at = Column(DateTime, default=datetime.utcnow)
    country_code = Column(String(2), default="ES")  # ISO 3166-1 alpha-2
    
    letterboxd_username = Column(String(50), nullable=True, index=True)  # Linked Letterboxd profile

    # Clerk auth (dual-auth transitional — legacy cookie still works while clerk_user_id is NULL)
    clerk_user_id = Column(String(255), unique=True, nullable=True, index=True)
    is_anonymous = Column(Boolean, nullable=False, server_default="false")

    # Onboarding (carousel cold-start flow)
    tag_preferences = Column(JSONB, nullable=True)  # {"avoided": [...]}
    onboarding_completed = Column(Boolean, nullable=False, server_default="false")
    onboarding_ratings_count = Column(Integer, nullable=False, server_default="0")
    
    # Relationships
    ratings = relationship("UserRating", back_populates="user", cascade="all, delete-orphan")
    clusters = relationship("UserCluster", back_populates="user", cascade="all, delete-orphan")
    streaming_providers = relationship("StreamingProvider", back_populates="user", cascade="all, delete-orphan")


class Movie(Base):
    """Movie metadata cache from TMDB"""
    __tablename__ = "movies"
    
    id = Column(Integer, primary_key=True, index=True)
    tmdb_id = Column(Integer, unique=True, nullable=False, index=True)
    title = Column(String(500), nullable=False)
    original_title = Column(String(500))
    year = Column(Integer, index=True)
    runtime = Column(Integer)  # minutes
    genres = Column(ARRAY(String))  # Array of genre names
    overview = Column(Text)
    poster_path = Column(String(255))
    backdrop_path = Column(String(255))
    vote_average = Column(Float)
    vote_count = Column(Integer)  # New: For popularity filtering
    popularity = Column(Float)
    original_language = Column(String(10))  # New: For language filtering
    keywords = Column(ARRAY(String))  # New: For vibe filtering
    letterboxd_uri = Column(String(500))  # From CSV
    letterboxd_rating = Column(Float)  # Scraped from Popular Chart
    directors = Column(ARRAY(String))  # Signal B: Auteur Expert
    cast = Column(ARRAY(String))  # Top 3 Cast Members
    
    # Phase 12: VectorBox Score & i18n
    imdb_id = Column(String(20), unique=True)
    imdb_rating = Column(Float)
    metacritic_rating = Column(Integer)
    rotten_tomatoes_rating = Column(Integer)
    vectorbox_score = Column(Float)
    title_es = Column(String(500))
    overview_es = Column(Text)
    collection_id = Column(Integer, index=True)  # New: For Franchise Bias fix
    release_dates = Column(JSONB)  # Localized release dates map
    imdb_vote_count = Column(Integer, nullable=True)
    has_enriched_embedding = Column(Boolean, default=False, server_default="false")  # LLM-enriched vector
    enriched_by_model = Column(String, nullable=True)  # Stores the Groq model ID used to generate the cinematic embedding description
    cinematic_description = Column(Text, nullable=True)  # LLM-generated cinematic description used for embedding
    embedding_quality_score = Column(Float, nullable=True)  # Cosine sim of stored Qdrant vector vs MiniLM reference (0-1). NULL = unchecked.

    # Release tracking
    release_date_us = Column(Date, nullable=True)
    release_date_es = Column(Date, nullable=True)
    release_date_ww = Column(Date, nullable=True)
    is_upcoming = Column(Boolean, nullable=False, server_default="false")

    # Metadata freshness
    last_metadata_refresh = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    ratings = relationship("UserRating", back_populates="movie")
    
    # Indexes for common queries
    __table_args__ = (
        Index('idx_movie_year_genre', 'year', 'genres'),
        Index('idx_movie_tmdb', 'tmdb_id'),
    )


class UserRating(Base):
    """User ratings from Letterboxd"""
    __tablename__ = "user_ratings"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    movie_id = Column(Integer, ForeignKey("movies.id", ondelete="CASCADE"), nullable=False)
    rating = Column(Float)  # 0.5 to 5.0 stars
    is_watchlist = Column(Boolean, default=False)
    is_liked = Column(Boolean, default=False)
    is_watched = Column(Boolean, default=False)
    is_rejected = Column(Boolean, default=False, server_default="false")  # "Not Interested" rejection
    watched_date = Column(DateTime)
    watch_count = Column(Integer, default=1, server_default="1")
    review = Column(Text)  # Optional review text
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    user = relationship("User", back_populates="ratings")
    movie = relationship("Movie", back_populates="ratings")
    
    # Unique constraint: one rating per user per movie
    __table_args__ = (
        Index('idx_user_movie', 'user_id', 'movie_id', unique=True),
    )


class UserCluster(Base):
    """K-Means clusters representing user taste profiles"""
    __tablename__ = "user_clusters"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    cluster_id = Column(Integer, nullable=False)  # 0 to n_clusters-1
    cluster_label = Column(String(100))  # e.g., "80s Horror", "French Drama"
    movie_count = Column(Integer)  # Number of movies in this cluster
    avg_rating = Column(Float)  # Average rating for movies in this cluster
    dominant_genres = Column(ARRAY(String))
    sample_movie_ids = Column(ARRAY(Integer))  # Representative movies
    medoid_movie_id = Column(Integer, nullable=True)  # Internal Movie.id of the medoid film
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    user = relationship("User", back_populates="clusters")
    
    __table_args__ = (
        Index('idx_user_cluster', 'user_id', 'cluster_id', unique=True),
    )


class StreamingProvider(Base):
    """User's selected streaming services"""
    __tablename__ = "streaming_providers"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    provider_id = Column(Integer, nullable=False)  # TMDB provider ID
    provider_name = Column(String(100))  # e.g., "Netflix", "HBO Max"
    country_code = Column(String(2))  # ISO 3166-1 alpha-2
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    user = relationship("User", back_populates="streaming_providers")
    
    __table_args__ = (
        Index('idx_user_provider', 'user_id', 'provider_id', unique=True),
    )


class MovieAvailability(Base):
    """Cache for movie streaming availability by country"""
    __tablename__ = "movie_availability"
    
    id = Column(Integer, primary_key=True, index=True)
    movie_id = Column(Integer, ForeignKey("movies.id", ondelete="CASCADE"), nullable=False)
    country_code = Column(String(2), nullable=False)  # ISO 3166-1 alpha-2
    providers = Column(JSONB)  # List of provider names/IDs
    last_updated = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    movie = relationship("Movie", backref="availability")
    
    __table_args__ = (
        Index('idx_movie_country', 'movie_id', 'country_code', unique=True),
    )
