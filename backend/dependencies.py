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
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from functools import lru_cache
import logging
import jwt
import httpx

from config import get_db, CLERK_JWKS_URL
from models.database import User, UserRating
from models.schemas import TokenResponse


@lru_cache(maxsize=1)
def _get_clerk_jwks() -> dict:
    """Fetch Clerk's JWKS. Cached in-process; cleared on key miss."""
    if not CLERK_JWKS_URL:
        return {"keys": []}
    return httpx.get(CLERK_JWKS_URL, timeout=5).json()


def _resolve_clerk_public_key(token: str):
    kid = jwt.get_unverified_header(token).get("kid")
    for key in _get_clerk_jwks().get("keys", []):
        if key.get("kid") == kid:
            return jwt.algorithms.RSAAlgorithm.from_jwk(key)
    # Refresh once on miss (handles Clerk key rotation without container restart)
    _get_clerk_jwks.cache_clear()
    for key in _get_clerk_jwks().get("keys", []):
        if key.get("kid") == kid:
            return jwt.algorithms.RSAAlgorithm.from_jwk(key)
    return None


def _extract_clerk_email(payload: dict) -> str:
    """Clerk places email in a top-level `email` claim when the JWT template
    includes it. Older templates or default sessions may surface it under
    `primary_email_address`/`email_address`/`email_addresses[].email_address`.
    Return the first non-empty variant, or "" if none found.
    """
    for key in ("email", "primary_email_address", "email_address"):
        val = payload.get(key)
        if isinstance(val, str) and val:
            return val
    emails = payload.get("email_addresses")
    if isinstance(emails, list):
        for entry in emails:
            if isinstance(entry, dict):
                addr = entry.get("email_address")
                if isinstance(addr, str) and addr:
                    return addr
            elif isinstance(entry, str) and entry:
                return entry
    return ""


async def _username_is_free(db: AsyncSession, username: str) -> bool:
    existing = await db.execute(select(User.id).where(User.username == username))
    return existing.scalar_one_or_none() is None


async def _allocate_username(db: AsyncSession, base: str) -> str:
    candidate = base
    counter = 1
    while not await _username_is_free(db, candidate):
        candidate = f"{base}_{counter}"
        counter += 1
        if counter > 50:  # Unreachable in practice; guards against pathological input.
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Unable to allocate username",
            )
    return candidate


async def _create_clerk_user(
    db: AsyncSession, clerk_user_id: str, email: str, is_anonymous: bool,
    clerk_username: str = ""
) -> User:
    # Priority: clerk username claim → email prefix → guest_
    if clerk_username and not is_anonymous:
        base_username = clerk_username
    elif email and not is_anonymous:
        base_username = email.split("@")[0]
    else:
        base_username = f"guest_{clerk_user_id[-8:]}"

    username = await _allocate_username(db, base_username)
    user = User(
        username=username,
        email=(email if email and not is_anonymous else None),
        clerk_user_id=clerk_user_id,
        is_anonymous=is_anonymous,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _relink_clerk_user_email(
    db: AsyncSession, user: User, email: str, clerk_username: str = ""
) -> None:
    # Priority: clerk username claim → email prefix
    if clerk_username:
        base_username = clerk_username
    else:
        base_username = email.split("@")[0]
    new_username = await _allocate_username(db, base_username)
    user.username = new_username
    user.email = email
    try:
        await db.commit()
        await db.refresh(user)
        logger.info(f"[CLERK] Relinked user {user.id}: guest_ → {new_username}")
    except Exception as e:
        await db.rollback()
        logger.warning(f"[CLERK] Failed to relink user {user.id}: {e}")


async def _build_token_response(db: AsyncSession, user: User, token: str) -> TokenResponse:
    rating_count = await db.scalar(
        select(func.count(UserRating.id)).where(UserRating.user_id == user.id)
    )
    has_data = (rating_count or 0) > 0
    return TokenResponse(
        token=token,
        user_id=user.id,
        username=user.username,
        has_data=has_data,
        letterboxd_username=user.letterboxd_username,
    )


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db)
) -> TokenResponse:
    """
    Clerk-only auth: requires a valid Clerk JWT in the Authorization header.
    """
    auth_header = request.headers.get("Authorization", "")
    bearer = auth_header.split(" ", 1)[1] if auth_header.startswith("Bearer ") else ""

    if not (bearer.startswith("eyJ") and len(bearer) > 100 and CLERK_JWKS_URL):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    try:
        public_key = _resolve_clerk_public_key(bearer)
        if public_key is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unknown Clerk signing key",
            )
        payload = jwt.decode(
            bearer,
            public_key,
            algorithms=["RS256"],
            options={"verify_aud": False, "leeway": 60},
        )
        clerk_user_id = payload.get("sub")
        if not clerk_user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid Clerk token",
            )

        is_anonymous = clerk_user_id.startswith("anon_")
        email = _extract_clerk_email(payload)
        clerk_username = payload.get("username", "") or ""

        result = await db.execute(select(User).where(User.clerk_user_id == clerk_user_id))
        user = result.scalar_one_or_none()

        # Fallback: legacy user exists by email without clerk_user_id — adopt it.
        if user is None and email and not is_anonymous:
            result = await db.execute(select(User).where(User.email == email))
            user = result.scalar_one_or_none()
            if user is not None:
                user.clerk_user_id = clerk_user_id
                if user.username.startswith("guest_"):
                    # Priority: clerk username claim → email prefix
                    base = clerk_username if clerk_username else email.split("@")[0]
                    user.username = await _allocate_username(db, base)
                await db.commit()
                await db.refresh(user)

        if user is None:
            user = await _create_clerk_user(
                db, clerk_user_id, email, is_anonymous, clerk_username
            )
        elif (
            not is_anonymous
            and email
            and user.username.startswith("guest_")
            and not user.email
        ):
            # Lazy repair: user was provisioned before the email claim arrived.
            await _relink_clerk_user_email(db, user, email, clerk_username)

        return await _build_token_response(db, user, bearer)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Clerk JWT verification failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication failed",
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
