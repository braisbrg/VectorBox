"""
Auth router — Clerk-only.
Legacy PIN/cookie endpoints were removed; Clerk handles sign-in, sign-up, and
session lifecycle. `/me` remains to hydrate VectorBox user data after Clerk login.
"""
from fastapi import APIRouter, Depends

from models.schemas import TokenResponse
from dependencies import get_current_user

router = APIRouter()


@router.post("/logout")
async def logout():
    """No-op retained for transition; Clerk's signOut() invalidates the real session."""
    return {"message": "Logged out successfully"}


@router.get("/me", response_model=TokenResponse)
async def read_users_me(current_user: TokenResponse = Depends(get_current_user)):
    """Return the authenticated user's VectorBox profile."""
    return current_user
