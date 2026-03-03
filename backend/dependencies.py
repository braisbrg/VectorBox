from typing import AsyncGenerator, Optional
from services.tmdb_client import TMDBClient
from services.omdb_client import OMDbClient
from services.qdrant_service import QdrantService
from services.embedding_service import EmbeddingService
import logging

logger = logging.getLogger(__name__)

# Global Singleton Instances
_tmdb_client: Optional[TMDBClient] = None
_omdb_client: Optional[OMDbClient] = None
_qdrant_service: Optional[QdrantService] = None
_embedding_service: Optional[EmbeddingService] = None

async def get_tmdb_client() -> TMDBClient:
    """Singleton TMDB Client"""
    global _tmdb_client
    if _tmdb_client is None:
        logger.info("Initializing TMDBClient Singleton")
        _tmdb_client = TMDBClient()
    return _tmdb_client

async def get_omdb_client() -> OMDbClient:
    """Singleton OMDb Client"""
    global _omdb_client
    if _omdb_client is None:
        logger.info("Initializing OMDbClient Singleton")
        _omdb_client = OMDbClient()
    return _omdb_client

async def get_qdrant_service() -> QdrantService:
    """Singleton Qdrant Service"""
    global _qdrant_service
    if _qdrant_service is None:
        logger.info("Initializing QdrantService Singleton")
        _qdrant_service = QdrantService()
    return _qdrant_service

async def get_embedding_service() -> EmbeddingService:
    """Singleton Embedding Service"""
    global _embedding_service
    if _embedding_service is None:
        logger.info("Initializing EmbeddingService Singleton")
        _embedding_service = EmbeddingService()
    return _embedding_service

async def close_services():
    """Cleanup all singleton connections"""
    global _tmdb_client, _omdb_client
    
    if _tmdb_client:
        await _tmdb_client.aclose()
        _tmdb_client = None
        
    if _omdb_client:
        await _omdb_client.aclose()
        _omdb_client = None
        
    logger.info("All backend services closed.")


# Auth Dependencies
from fastapi import Request, HTTPException, status, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
import logging
import uuid

from config import get_db
from models.database import User, UserRating
from models.schemas import TokenResponse

async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db)
) -> TokenResponse:
    """
    Dependency to get the current authenticated user from cookie or header.
    """
    token = request.cookies.get("vectorbox_token")
    
    # Fallback to Authorization header (Bearer token)
    if not token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]
            
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated"
        )
    
    try:
        # Verify token
        try:
            token_uuid = uuid.UUID(token)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid session token format"
            )
        
        # Check DB
        result = await db.execute(select(User).where(User.secret_token == token_uuid))
        user = result.scalar_one_or_none()
        
        if not user:
             raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid session"
            )

        # Check data status
        from sqlalchemy import func
        rating_count = await db.scalar(
            select(func.count(UserRating.id)).where(UserRating.user_id == user.id)
        )
        has_data = (rating_count or 0) > 0

        return TokenResponse(
            token=str(user.secret_token),
            user_id=user.id,
            username=user.username,
            has_data=has_data,
            letterboxd_username=user.letterboxd_username
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Auth check failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication failed"
        )

async def verify_user_ownership(
    user_id: int, 
    current_user: TokenResponse = Depends(get_current_user)
) -> TokenResponse:
    """
    Enforce that the requested user_id matches the authenticated user.
    Prevents IDOR attacks.
    """
    if current_user.user_id != user_id:
        logger.warning(f"IDOR Attempt: User {current_user.user_id} tried to access User {user_id}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Access Denied: You do not own this resource."
        )
    return current_user
