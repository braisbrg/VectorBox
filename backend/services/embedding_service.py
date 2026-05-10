"""
Embedding generation service using Sentence Transformers
"""
from sentence_transformers import SentenceTransformer
from typing import List
import numpy as np
import logging

logger = logging.getLogger(__name__)


# Singleton instance
_model_instance = None

def get_model():
    """Lazy load the model only once"""
    global _model_instance
    if _model_instance is None:
        logger.info(f"Loading AI Model into Memory (Singleton): {EmbeddingService.MODEL_NAME}")
        _model_instance = SentenceTransformer(EmbeddingService.MODEL_NAME)
        logger.info("Model loaded successfully")
    return _model_instance

class EmbeddingService:
    """Generate embeddings for movie metadata"""
    
    MODEL_NAME = "all-MiniLM-L6-v2"  # Fast, 384 dimensions
    
    def __init__(self):
        # Model is now loaded lazily via get_model()
        pass
    
    def generate_embedding(self, movie_data: dict, include_title: bool = False, text_override: str = None) -> np.ndarray:
        """
        Generate embedding from movie metadata.
        If text_override is provided (e.g. from cinematic enricher), use it directly.
        Otherwise combines: overview, genres, keywords (and title if include_title=True).

        Default is `include_title=False` because title tokens cause off-theme
        neighbours (e.g. Howl's → The Howling/Witch films, Faster Faster → Fast X).
        Title-aware callers (e.g. the /movies title search endpoint) must opt in.
        """
        model = get_model()
        
        if text_override:
            combined_text = text_override
        else:
            # Build rich text representation (New Format)
            # Format: f"{title}. {overview}. Genres: {genres}. Themes: {keywords_string}"
            
            parts: List[str] = []
            
            if include_title and movie_data.get("title"):
                 parts.append(movie_data["title"])
                 
            if movie_data.get("overview"):
                parts.append(movie_data["overview"])
                
            if movie_data.get("genres"):
                genres = movie_data["genres"]
                if isinstance(genres, list) and genres:
                    parts.append(f"Genres: {', '.join(genres)}")
                    
            if movie_data.get("keywords"):
                keywords = movie_data["keywords"]
                if isinstance(keywords, list) and keywords:
                    # Limit to top 15 keywords to prevent noise
                    kw_str = ", ".join(keywords[:15])
                    parts.append(f"Themes: {kw_str}")
            
            combined_text = ". ".join(parts)
        
        if not combined_text:
            raise ValueError("No text available for embedding generation")
        
        # Generate embedding
        embedding = model.encode(combined_text, convert_to_numpy=True)
        
        return embedding
    
    def generate_batch_embeddings(self, movies_data: List[dict], include_title: bool = False) -> List[np.ndarray]:
        """Generate embeddings for multiple movies (more efficient).

        Default `include_title=False` matches `generate_embedding` to avoid the
        title-token leakage that pollutes neighbour search.
        """
        model = get_model()
        texts = []

        for movie in movies_data:
            parts = []

            if include_title and movie.get("title"):
                parts.append(movie["title"])

            if movie.get("overview"):
                parts.append(movie["overview"])
            
            if movie.get("genres"):
                genres = movie["genres"]
                if isinstance(genres, list) and genres:
                    parts.append(f"Genres: {', '.join(genres)}")
            
            if movie.get("keywords"):
                keywords = movie["keywords"]
                if isinstance(keywords, list) and keywords:
                    kw_str = ", ".join(keywords[:15])
                    parts.append(f"Themes: {kw_str}")
            
            combined_text = ". ".join(parts)
            texts.append(combined_text if combined_text else movie.get("title", "Unknown"))
        
        # Batch encoding (much faster)
        embeddings = model.encode(texts, convert_to_numpy=True, show_progress_bar=True)
        
        return embeddings
