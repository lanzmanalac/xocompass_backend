# api/routers/auth.py
"""
Authentication HTTP surface. Five endpoints; thin wrappers over
services/auth_service.py. NO business logic in this file — every decision
is delegated to the service. The router's only jobs are:
  1. Validate the request body via Pydantic (api/schemas.py).
  2. Call the service.
  3. Translate service-layer exceptions to HTTP responses.
  4. Shape the wire response.

Why this separation matters (ISO 25010 → Maintainability → Modularity):
  Auth logic is unit-testable against a SQLite session — no FastAPI test
  client needed. The router is e2e-testable via httpx.AsyncClient. Two
  layers, two test surfaces, no overlap.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from api.dependencies.auth import (
    DbDep,
    CurrentUserDep,
    get_current_user,
)
from api.schemas import (
    AuthenticatedUser,
    LoginRequest,
    LoginResponse,
    LogoutRequest,
    LogoutResponse,
    MeResponse,
    RefreshRequest,
    RefreshResponse,
    RegisterRequest,
    RegisterResponse,
)
from services import auth_service
from services.auth_service import (
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    InvalidInviteError,
    InvalidTokenError,
    LoginResult,
    TokenPair,
    TokenReplayError,
)

# Phase 6 Rate Limiter
from core.rate_limit import limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


# ═════════════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════════════


def _client_context(request: Request) -> tuple[str | None, str | None]:
    """Pull (ip, user_agent) from the request without re-parsing logic
    that lives in api/dependencies/auth._client_ip — but for endpoints
    where Depends(get_current_user) hasn't run, we extract here directly.
    """
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        ip = fwd.split(",")[0].strip() or None
    else:
        ip = request.client.host if request.client else None
    return ip, request.headers.get("user-agent")


def _to_authenticated_user(result: LoginResult) -> AuthenticatedUser:
    return AuthenticatedUser(
        id=result.user.id,
        email=result.user.email,
        full_name=result.user.full_name,
        role=result.user.role.value,  # Enum → str
        is_active=result.user.is_active,
    )


def _to_login_response(result: LoginResult) -> LoginResponse:
    t = result.tokens
    return LoginResponse(
        access_token=t.access_token,
        refresh_token=t.refresh_token,
        access_expires_at=t.access_expires_at,
        refresh_expires_at=t.refresh_expires_at,
        user=_to_authenticated_user(result),
    )


def _to_refresh_response(t: TokenPair) -> RefreshResponse:
    return RefreshResponse(
        access_token=t.access_token,
        refresh_token=t.refresh_token,
        access_expires_at=t.access_expires_at,
        refresh_expires_at=t.refresh_expires_at,
    )


# ═════════════════════════════════════════════════════════════════════════════
# Endpoints
# ═════════════════════════════════════════════════════════════════════════════


@router.post(
    "/login",
    response_model=LoginResponse,
    status_code=status.HTTP_200_OK,
    summary="Authenticate with email and password.",
)

@limiter.limit("5/15 minutes")
def login(
    request: Request,
    body: LoginRequest,
    db: DbDep,
) -> LoginResponse:
    """
    On success: 200 with access + refresh token pair and the user payload.
    On any failure: 401 with the standard error envelope. NO enumeration —
    "user not found" and "wrong password" are indistinguishable on the wire.
    """
    ip, ua = _client_context(request)
    try:
        result = auth_service.authenticate_user(
            db, email=body.email, password=body.password,
            ip_address=ip, user_agent=ua,
        )
    except InvalidCredentialsError:
        # Generic 401. The audit row was already written by the service.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    return _to_login_response(result)


@router.post(
    "/refresh",
    response_model=RefreshResponse,
    status_code=status.HTTP_200_OK,
    summary="Rotate a refresh token for a fresh access + refresh pair.",
)
def refresh(
    request: Request,
    body: RefreshRequest,
    db: DbDep,
) -> RefreshResponse:
    """
    Industry-standard refresh-token ROTATION with REPLAY DETECTION.
    See services/auth_service.refresh_session for the full state machine.
    """
    ip, ua = _client_context(request)
    try:
        tokens = auth_service.refresh_session(
            db, refresh_token=body.refresh_token,
            ip_address=ip, user_agent=ua,
        )
    except TokenReplayError:
        # Replay is logged at WARNING by the service. Wire response is
        # the same opaque 401 — we don't tell the attacker we noticed.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token.",
        )
    except InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token.",
        )

    return _to_refresh_response(tokens)


@router.post(
    "/logout",
    response_model=LogoutResponse,
    status_code=status.HTTP_200_OK,
    summary="Revoke the presented refresh token.",
)
def logout(
    body: LogoutRequest,
    request: Request,
    db: DbDep,
) -> LogoutResponse:
    """
    Idempotent. A missing or already-consumed token returns 200 — the
    frontend just clears its local storage. The access token is NOT
    revoked; it expires naturally within ACCESS_TOKEN_EXPIRE_MINUTES.
    """
    ip, ua = _client_context(request)
    auth_service.logout(
        db, refresh_token=body.refresh_token,
        ip_address=ip, user_agent=ua,
    )
    return LogoutResponse()


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Consume an invite token and create the User row.",
)
def register(
    body: RegisterRequest,
    request: Request,
    db: DbDep,
) -> RegisterResponse:
    """
    Used by the invitee who clicked the invite URL. On success, the User
    row is created AND they are auto-logged-in (returned token pair).
    On any invite failure, generic 400 — invalid/expired/consumed are
    indistinguishable to the wire.
    """
    ip, ua = _client_context(request)
    try:
        result = auth_service.consume_invite(
            db,
            raw_token=body.invite_token,
            full_name=body.full_name,
            password=body.password,
            ip_address=ip,
            user_agent=ua,
        )
    except InvalidInviteError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired invitation.",
        )
    except EmailAlreadyRegisteredError:
        # Different status code — the invite was valid, the email is the
        # problem. Still no enumeration concern: the invitee already
        # knows their own email.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account already exists for this email.",
        )

    return _to_login_response(result)


@router.get(
    "/me",
    response_model=MeResponse,
    summary="Return the current authenticated user.",
)
def me(user: CurrentUserDep) -> MeResponse:
    """
    Used by the frontend on app boot to hydrate its auth context.
    Resolves identity STRICTLY from the JWT (via get_current_user) +
    one DB read for freshness. No tokens here — frontend already has them.
    """
    return MeResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role.value,
        is_active=user.is_active,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
    )