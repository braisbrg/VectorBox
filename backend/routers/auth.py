"""
Authentication router for VectorBox v1.1
Netflix-style profile system with username + 4-digit PIN
"""
from fastapi import APIRouter, Depends, HTTPException, status, Response, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
import logging
import uuid
import os
from passlib.hash import bcrypt

from config import get_db
from models.database import User

IS_PRODUCTION = os.getenv("ENVIRONMENT", "development") == "production"
from models.schemas import RegisterRequest, LoginRequest, TokenResponse
from limiter import limiter

logger = logging.getLogger(__name__)
router = APIRouter()


def hash_pin(pin: str) -> str:
    """Hash a 4-digit PIN using passlib bcrypt"""
    return bcrypt.hash(pin)


def verify_pin(pin: str, pin_hash: str) -> bool:
    """Verify a PIN against its hash"""
    return bcrypt.verify(pin, pin_hash)


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/hour")
async def register(
    request: Request,
    user_request: RegisterRequest,
    response: Response,
    db: AsyncSession = Depends(get_db)
):
    """
    Register a new VectorBox user with username and 4-digit PIN.
    Returns a session token for authentication.
    """
    try:
        # Normalize username
        username = user_request.username.lower().strip()

        # Check if username already exists
        result = await db.execute(select(User).where(User.username == username))
        if result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username already taken"
            )
        
        # Create user with hashed PIN and session token
        secret_token = uuid.uuid4()
        new_user = User(
            username=username,
            pin_hash=hash_pin(user_request.pin),
            secret_token=secret_token,
            country_code=user_request.country_code.upper()
        )
        
        db.add(new_user)
        try:
            await db.commit()
            await db.refresh(new_user)
        except Exception as e:
            await db.rollback()
            logger.error(f"DB commit failed during registration: {e}")
            raise
        
        logger.info(f"Registered new user: {new_user.username} (ID: {new_user.id})")
        
        # Set session cookie
        response.set_cookie(
            key="vectorbox_token",
            value=str(secret_token),
            httponly=True,
            secure=IS_PRODUCTION,
            samesite="lax",
            max_age=60 * 60 * 24 * 365  # 1 year
        )
        
        return TokenResponse(
            token=str(secret_token),
            user_id=new_user.id,
            username=new_user.username
        )
        
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Registration failed due to database constraint"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error during registration: {e}")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        )


@router.post("/login", response_model=TokenResponse)
@limiter.limit("5/minute")
async def login(
    request: Request,
    user_request: LoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db)
):
    """
    Login with username and 4-digit PIN.
    Returns existing or new session token.
    """
    try:
        # Normalize username
        username = user_request.username.lower().strip()

        # Find user by username
        result = await db.execute(select(User).where(User.username == username))
        user = result.scalar_one_or_none()
        
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid username or PIN"
            )
        
        # Verify PIN
        if not user.pin_hash or not verify_pin(user_request.pin, user.pin_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid username or PIN"
            )
        
        if not user.secret_token:
            user.secret_token = uuid.uuid4()
            try:
                await db.commit()
                await db.refresh(user)
            except Exception as e:
                await db.rollback()
                logger.error(f"DB commit failed during token generation: {e}")
                raise
        
        # v1.1: Check if user has data (Onboarding Jail)
        from sqlalchemy import func
        from models.database import UserRating
        
        rating_count = await db.scalar(
            select(func.count(UserRating.id)).where(UserRating.user_id == user.id)
        )
        has_data = (rating_count or 0) > 0
        
        # Security: Rotate session token on login to prevent session fixation
        user.secret_token = uuid.uuid4()
        try:
            await db.commit()
            await db.refresh(user)
        except Exception as e:
            await db.rollback()
            logger.error(f"DB commit failed during session rotation: {e}")
            raise
        
        logger.info(f"User logged in: {user.username} (ID: {user.id}, Data: {has_data})")
        
        # Set session cookie
        response.set_cookie(
            key="vectorbox_token",
            value=str(user.secret_token),
            httponly=True,
            secure=IS_PRODUCTION,
            samesite="lax",
            max_age=60 * 60 * 24 * 365  # 1 year
        )
        
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
        logger.error(f"Error during login: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        )


@router.post("/logout")
async def logout(response: Response):
    """
    Clear session cookie.
    """
    response.delete_cookie(key="vectorbox_token")
    return {"message": "Logged out successfully"}



from dependencies import get_current_user

@router.get("/me", response_model=TokenResponse)
async def read_users_me(current_user: TokenResponse = Depends(get_current_user)):
    """
    Get current user profile based on cookie.
    """
    return current_user
