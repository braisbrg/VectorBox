from datetime import timedelta
from typing import Optional
import httpx
from fastapi import Request
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


async def get_redis(request: Request):
    """
    Returns the app-scoped Redis singleton stored by the lifespan handler.
    Falls back to None if Redis is unavailable (never raises — callers must
    guard against None to ensure graceful degradation).
    """
    return getattr(request.app.state, 'redis', None)


async def get_http_client(request: Request) -> httpx.AsyncClient:
    """Returns the global lifespan httpx.AsyncClient."""
    return request.app.state.http_client


# Auth Dependencies
from fastapi import Request, HTTPException, status, Depends
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
import asyncio
import time
import logging
import jwt
import httpx

from config import get_db, CLERK_JWKS_URL, CLERK_ISSUER
from models.database import User, UserRating
from models.schemas import TokenResponse


# ---------------------------------------------------------------------------
# Clerk JWKS — async, bounded, and NOT attacker-triggerable (SEC-1 / REL-3)
#
# Previously this was a SYNC httpx.get inside an lru_cache, called from the
# async auth path, and `_resolve_clerk_public_key` did cache_clear()+refetch on
# ANY unknown `kid`. A flood of tokens carrying random `kid`s therefore forced
# repeated *blocking* network calls on the event loop (unauthenticated soft-DoS)
# and hammered Clerk. Now:
#   - the fetch is async (httpx.AsyncClient) so it never blocks the loop,
#   - a successful JWKS is cached in-process for _JWKS_TTL,
#   - the on-miss refetch is rate-limited by _JWKS_MIN_REFRESH so an attacker
#     spraying bogus kids can trigger at most one network call per cooldown.
# ---------------------------------------------------------------------------
_JWKS_TTL = 3600.0          # serve cached JWKS for up to 1h on the happy path
_JWKS_MIN_REFRESH = 30.0    # ...but allow a forced refresh at most every 30s
_jwks_cache: dict = {"keys": []}
_jwks_fetched_at: float = 0.0
_jwks_last_refresh_attempt: float = 0.0
_jwks_lock = asyncio.Lock()


async def _fetch_clerk_jwks() -> dict:
    if not CLERK_JWKS_URL:
        return {"keys": []}
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(CLERK_JWKS_URL)
        resp.raise_for_status()
        return resp.json()


async def _get_clerk_jwks(force: bool = False) -> dict:
    """Return Clerk's JWKS, cached with a TTL. `force=True` requests a refresh
    but is itself rate-limited (_JWKS_MIN_REFRESH) so it can't be weaponised."""
    global _jwks_cache, _jwks_fetched_at, _jwks_last_refresh_attempt
    now = time.monotonic()
    fresh = (now - _jwks_fetched_at) < _JWKS_TTL
    if _jwks_cache.get("keys") and fresh and not force:
        return _jwks_cache
    # A forced refresh (key miss) is throttled regardless of how many requests ask.
    if force and (now - _jwks_last_refresh_attempt) < _JWKS_MIN_REFRESH:
        return _jwks_cache
    async with _jwks_lock:
        now = time.monotonic()
        # Re-check inside the lock: another coroutine may have just refreshed.
        if _jwks_cache.get("keys") and (now - _jwks_fetched_at) < _JWKS_TTL and not force:
            return _jwks_cache
        if force and (now - _jwks_last_refresh_attempt) < _JWKS_MIN_REFRESH:
            return _jwks_cache
        _jwks_last_refresh_attempt = now
        try:
            data = await _fetch_clerk_jwks()
            if data.get("keys"):
                _jwks_cache = data
                _jwks_fetched_at = now
        except Exception as e:
            logger.warning(f"Clerk JWKS fetch failed: {e}")
        return _jwks_cache


async def _resolve_clerk_public_key(token: str):
    kid = jwt.get_unverified_header(token).get("kid")
    if not kid:
        return None
    for key in (await _get_clerk_jwks()).get("keys", []):
        if key.get("kid") == kid:
            return jwt.algorithms.RSAAlgorithm.from_jwk(key)
    # Unknown kid: refresh ONCE (rate-limited) to handle Clerk key rotation
    # without a container restart. Bogus-kid floods can't escalate past the
    # _JWKS_MIN_REFRESH cooldown, so the loop is never spammed with fetches.
    for key in (await _get_clerk_jwks(force=True)).get("keys", []):
        if key.get("kid") == kid:
            return jwt.algorithms.RSAAlgorithm.from_jwk(key)
    return None


def _extract_clerk_email(payload: dict) -> str:
    """Clerk places email in a top-level `email` claim when the JWT template
    includes it. Older templates or default sessions may surface it under
    `primary_email_address`/`email_address`/`email_addresses[].email_address`.
    Return the first non-empty variant, or "" if none found.

    Verification: when the claim ships with a verification status (Clerk's
    `email_addresses[]` shape does), we only accept entries marked verified.
    Top-level scalar claims are trusted iff `email_verified` is True or absent
    (Clerk's default JWT template only emits the `email` claim post-verification).
    """
    emails = payload.get("email_addresses")
    if isinstance(emails, list):
        for entry in emails:
            if isinstance(entry, dict):
                verification = entry.get("verification") or {}
                if verification.get("status") and verification.get("status") != "verified":
                    continue
                addr = entry.get("email_address")
                if isinstance(addr, str) and addr:
                    return addr
            elif isinstance(entry, str) and entry:
                return entry

    # If a verification flag is present and explicitly False, refuse the claim.
    if payload.get("email_verified") is False:
        return ""
    for key in ("email", "primary_email_address", "email_address"):
        val = payload.get(key)
        if isinstance(val, str) and val:
            return val
    return ""


def _email_is_proven_verified(payload: dict) -> bool:
    """Strict counterpart to _extract_clerk_email: True only with POSITIVE
    evidence of verification in the token.

    _extract_clerk_email is deliberately permissive because its result also
    drives cosmetic things (username seed, display). That permissiveness is
    unsafe for exactly one decision — adopting a pre-existing DB row by email —
    because that binds a new Clerk identity to someone else's data. The old
    code treated "no verification field" as verified, so a JWT template
    emitting a bare `email` claim (or an `email_addresses[]` entry without a
    `verification` block) was enough to take over a legacy account.

    Absence of evidence is not evidence of verification: default False.
    """
    if payload.get("email_verified") is True:
        return True
    emails = payload.get("email_addresses")
    if isinstance(emails, list):
        for entry in emails:
            if isinstance(entry, dict) and (entry.get("verification") or {}).get("status") == "verified":
                return True
    return False


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
    # Pre-check: a different Clerk account ALREADY owns this email.
    # The legacy-adoption path (only runs when clerk_user_id IS NULL on
    # the existing row, per H-1 fix) doesn't apply here. INSERTing would
    # blow up on the unique constraint on users.email, then mask itself
    # as a 401 to the client (because the exception bubbles up through
    # get_current_user → "Authentication failed"). Return 409 explicitly
    # with a message the frontend can act on (redirect to log-in).
    if email and not is_anonymous:
        existing = await db.execute(
            select(User).where(User.email == email, User.clerk_user_id.is_not(None))
        )
        owner = existing.scalar_one_or_none()
        if owner is not None and owner.clerk_user_id != clerk_user_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="email_already_registered",
            )

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
    try:
        await db.commit()
        await db.refresh(user)
        return user
    except IntegrityError:
        # Defensive backstop: a concurrent request just inserted the same
        # email between our pre-check and this commit (race on first signup
        # when no prior row exists). Roll back, fetch whichever row landed
        # first, and return it. Avoids the 401 cascade the user saw when
        # two parallel claim-anonymous calls hit the same brand-new email.
        await db.rollback()
        if email:
            existing = (await db.execute(
                select(User).where(User.email == email)
            )).scalar_one_or_none()
            if existing is not None:
                logger.warning(
                    f"[CLERK] Concurrent insert race resolved — adopted "
                    f"existing user.id={existing.id} for email={email}"
                )
                return existing
        # Genuinely unexpected (no email, or some other constraint) — re-raise
        # and let get_current_user surface it as 401.
        raise


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
        public_key = await _resolve_clerk_public_key(bearer)
        if public_key is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unknown Clerk signing key",
            )
        # SEC-4: pin the issuer (when configured) so a validly-signed token from
        # a *different* Clerk instance can't be replayed here. `aud` stays
        # unverified — Clerk session tokens don't carry an audience claim.
        decode_kwargs = dict(
            algorithms=["RS256"],
            options={"verify_aud": False},
            leeway=timedelta(seconds=60),
        )
        if CLERK_ISSUER:
            decode_kwargs["issuer"] = CLERK_ISSUER
        payload = jwt.decode(bearer, public_key, **decode_kwargs)
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
        # Hardening (H-1 from 2026-05 security audit):
        #   - email must come from _extract_clerk_email (verified-only filter)
        #   - target row's clerk_user_id MUST already be NULL — otherwise a new
        #     Clerk user signing up with another Clerk user's email could
        #     overwrite the existing binding and steal that account's data.
        #   - the email must be PROVABLY verified in the token (fail closed);
        #     "no verification field present" used to count as verified, which
        #     made this an account-takeover path for any JWT template that
        #     emits a bare `email` claim.
        if user is None and email and not is_anonymous and _email_is_proven_verified(payload):
            result = await db.execute(
                select(User).where(
                    User.email == email,
                    User.clerk_user_id.is_(None),
                )
            )
            user = result.scalar_one_or_none()
            if user is not None:
                logger.warning(
                    f"[CLERK] Adopting legacy user {user.id} (email={email}) "
                    f"into clerk_user_id={clerk_user_id}. Verify Clerk JWT "
                    f"template only emits VERIFIED emails — this is a soft "
                    f"account-takeover vector if the template emits unverified."
                )
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


async def get_optional_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Optional[TokenResponse]:
    """
    Like get_current_user but returns None when no valid auth header is present.
    Used by /onboarding/movies to serve both guests and signed-in users.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    try:
        return await get_current_user(request, db)
    except HTTPException:
        return None


# ---------------------------------------------------------------------------
# Anonymous Session Cookie — vb_anon_session
# ---------------------------------------------------------------------------
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from config import ANON_SESSION_SECRET, ANON_SESSION_MAX_AGE

_anon_signer = URLSafeTimedSerializer(ANON_SESSION_SECRET, salt="vb_anon_session")

ANON_COOKIE_NAME = "vb_anon_session"


def sign_anon_session(user_id: int) -> str:
    """Create a signed cookie value for an anonymous user."""
    return _anon_signer.dumps({"uid": user_id})


def verify_anon_session(token: str) -> Optional[int]:
    """Validate and extract user_id from a signed anonymous cookie. Returns None on failure."""
    try:
        data = _anon_signer.loads(token, max_age=ANON_SESSION_MAX_AGE)
        return data.get("uid")
    except (BadSignature, SignatureExpired):
        return None


async def get_anonymous_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    """
    Resolve the anonymous user from the vb_anon_session httponly cookie.
    Returns None if cookie is absent, invalid, expired, or user not found.
    Never raises — callers decide.
    """
    cookie_value = request.cookies.get(ANON_COOKIE_NAME)
    if not cookie_value:
        return None

    user_id = verify_anon_session(cookie_value)
    if user_id is None:
        return None

    result = await db.execute(
        select(User).where(User.id == user_id, User.is_anonymous.is_(True))
    )
    return result.scalar_one_or_none()


async def get_current_or_anonymous_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """
    Unified auth: tries Clerk JWT first, falls back to vb_anon_session cookie.
    Raises 401 only if neither is present/valid.
    """
    # Path 1: Clerk JWT (authenticated user)
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        try:
            return await get_current_user(request, db)
        except HTTPException:
            pass  # Fall through to anonymous path

    # Path 2: Anonymous session cookie
    anon_user = await get_anonymous_user(request, db)
    if anon_user is not None:
        rating_count = await db.scalar(
            select(func.count(UserRating.id)).where(UserRating.user_id == anon_user.id)
        )
        has_data = (rating_count or 0) > 0
        return TokenResponse(
            token="",  # No JWT for anonymous users
            user_id=anon_user.id,
            username=anon_user.username,
            has_data=has_data,
            letterboxd_username=None,
        )

    # Neither path succeeded
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
    )
