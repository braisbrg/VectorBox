"""
Embedding generation service using Sentence Transformers
"""
from sentence_transformers import SentenceTransformer
from typing import List
import numpy as np
import logging

logger = logging.getLogger(__name__)


class EmbeddingService:
    """Generate embeddings for movie metadata"""
    
    MODEL_NAME = "all-MiniLM-L6-v2"  # Fast, 384 dimensions
    
    def __init__(self):
        logger.info(f"Loading embedding model: {self.MODEL_NAME}")
        self.model = SentenceTransformer(self.MODEL_NAME)
        logger.info("Model loaded successfully")
    
    def generate_embedding(self, movie_data: dict, include_title: bool = True) -> np.ndarray:
        """
        Generate embedding from movie metadata
        Combines: title, overview, genres, keywords
        """
        # Build rich text representation
        text_parts = []
        
        # Title (weighted more)
        if include_title and movie_data.get("title"):
            text_parts.append(movie_data["title"])
            text_parts.append(movie_data["title"])  # Duplicate for emphasis
        
        # Overview/synopsis
        if movie_data.get("overview"):
            text_parts.append(movie_data["overview"])
        
        # Genres (important for clustering)
        if movie_data.get("genres"):
            genres = movie_data["genres"]
            if isinstance(genres, list):
                genre_text = " ".join(genres)
                text_parts.append(genre_text)
                text_parts.append(genre_text)  # Duplicate for emphasis
        
        # Keywords
        if movie_data.get("keywords"):
            keywords = movie_data["keywords"]
            if isinstance(keywords, list):
                text_parts.append(" ".join(keywords))
        
        # Combine all parts
        combined_text = ". ".join(filter(None, text_parts))
        
        if not combined_text:
            raise ValueError("No text available for embedding generation")
        
        # Generate embedding
        embedding = self.model.encode(combined_text, convert_to_numpy=True)
        
        return embedding
    
    def generate_batch_embeddings(self, movies_data: List[dict]) -> List[np.ndarray]:
        """Generate embeddings for multiple movies (more efficient)"""
        texts = []
        
        for movie in movies_data:
            text_parts = []
            
            if movie.get("title"):
                text_parts.append(movie["title"])
                text_parts.append(movie["title"])
            
            if movie.get("overview"):
                text_parts.append(movie["overview"])
            
            if movie.get("genres"):
                genres = movie["genres"]
                if isinstance(genres, list):
                    genre_text = " ".join(genres)
                    text_parts.append(genre_text)
                    text_parts.append(genre_text)
            
            if movie.get("keywords"):
                keywords = movie["keywords"]
                if isinstance(keywords, list):
                    text_parts.append(" ".join(keywords))
            
            combined_text = ". ".join(filter(None, text_parts))
            texts.append(combined_text if combined_text else movie.get("title", "Unknown"))
        
        # Batch encoding (much faster)
        embeddings = self.model.encode(texts, convert_to_numpy=True, show_progress_bar=True)
        
        return embeddings
