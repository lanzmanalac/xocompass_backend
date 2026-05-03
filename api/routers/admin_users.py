# api/routers/admin_users.py
"""
Admin User Management — Tab 1, Card 1.

Endpoints:
  GET    /admin/users
  GET    /admin/users/{user_id}
  PATCH  /admin/users/{user_id}
  POST   /admin/users/{user_id}/activate
  POST   /admin/users/{user_id}/deactivate

All endpoints require Admin role.
"""

from __future__ import annotations

from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import EmailStr  # noqa: F401  (kept for response model imports)

from api.dependencies.auth import DbDep, CurrentUserDep, require_admin
from api.schemas import (
    AdminUserDetailResponse,
    AdminUserListItem,
    AdminUserListResponse,
    UpdateUserRequest,
    UserStatusResponse,
    AdminInitiateResetResponse,
)
from domain.auth_models import User, UserRole
from repository import auth_repository as repo
from services import auth_service
from services.auth_service import LastAdminError

router = APIRouter(prefix="/admin/users", tags=["admin-users"])


def _to_list_item(u: User) -> AdminUserListItem:
    return AdminUserListItem(
        id=u.id,
        email=u.email,
        full_name=u.full_name,
        role=u.role.value,
        is_active=u.is_active,
        last_login_at=u.last_login_at,
        created_at=u.created_at,
        created_by_user_id=u.created_by_user_id,
    )


def _to_detail(u: User) -> AdminUserDetailResponse:
    return AdminUserDetailResponse(
        id=u.id,
        email=u.email,
        full_name=u.full_name,
        role=u.role.value,
        is_active=u.is_active,
        last_login_at=u.last_login_at,
        created_at=u.created_at,
        updated_at=u.updated_at,
        created_by_user_id=u.created_by_user_id,
    )


# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "",
    response_model=AdminUserListResponse,
    summary="List users with optional filters.",
)
def list_users(
    db: DbDep,
    _admin: Annotated[User, Depends(require_admin)],
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    role_filter: Optional[str] = Query(None, alias="role"),
    status_filter: Optional[str] = Query(None, alias="status"),
    search: Optional[str] = Query(None, max_length=120),
):
    role_enum: Optional[UserRole] = None
    if role_filter:
        try:
            role_enum = UserRole(role_filter.upper())
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown role filter: {role_filter!r}",
            )

    is_active_filter: Optional[bool] = None
    if status_filter == "active":
        is_active_filter = True
    elif status_filter == "inactive":
        is_active_filter = False
    elif status_filter is not None:
        raise HTTPException(
            status_code=400,
            detail="status filter must be 'active' or 'inactive'.",
        )

    rows, total = repo.list_users_paginated(
        db,
        page=page,
        page_size=page_size,
        role=role_enum,
        is_active=is_active_filter,
        search=search,
    )
    return AdminUserListResponse(
        items=[_to_list_item(u) for u in rows],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get(
    "/{user_id}",
    response_model=AdminUserDetailResponse,
    summary="Single user detail.",
)
def get_user(
    user_id: UUID,
    db: DbDep,
    _admin: Annotated[User, Depends(require_admin)],
):
    user = repo.fetch_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    return _to_detail(user)


@router.patch(
    "/{user_id}",
    response_model=AdminUserDetailResponse,
    summary="Partial update of a user (full_name and/or role).",
)
def patch_user(
    user_id: UUID,
    body: UpdateUserRequest,
    request: Request,
    db: DbDep,
    admin: Annotated[User, Depends(require_admin)],
):
    user = repo.fetch_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")

    role_enum: Optional[UserRole] = None
    if body.role is not None:
        role_enum = UserRole(body.role)

    try:
        auth_service.admin_update_user(
            db,
            actor=admin,
            target_user=user,
            full_name=body.full_name,
            role=role_enum,
            request=request,
        )
    except LastAdminError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    return _to_detail(user)


@router.post(
    "/{user_id}/activate",
    response_model=UserStatusResponse,
    summary="Activate a user (sets is_active=true).",
)
def activate_user(
    user_id: UUID,
    request: Request,
    db: DbDep,
    admin: Annotated[User, Depends(require_admin)],
):
    user = repo.fetch_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")

    try:
        auth_service.admin_set_user_active(
            db, actor=admin, target_user=user, active=True, request=request,
        )
    except LastAdminError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    return UserStatusResponse(id=user.id, email=user.email, is_active=user.is_active)


@router.post(
    "/{user_id}/deactivate",
    response_model=UserStatusResponse,
    summary="Deactivate a user. Revokes all their refresh tokens.",
)
def deactivate_user(
    user_id: UUID,
    request: Request,
    db: DbDep,
    admin: Annotated[User, Depends(require_admin)],
):
    user = repo.fetch_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")

    try:
        auth_service.admin_set_user_active(
            db, actor=admin, target_user=user, active=False, request=request,
        )
    except LastAdminError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    return UserStatusResponse(id=user.id, email=user.email, is_active=user.is_active)

# ═════════════════════════════════════════════════════════════════════════════
# Admin: Password Reset and Soft-Delete
# ═════════════════════════════════════════════════════════════════════════════


@router.post(
    "/{user_id}/reset-password",
    response_model=AdminInitiateResetResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Admin-initiated password reset. Returns a one-time reset URL.",
)
def admin_reset_password(
    user_id: UUID,
    request: Request,
    db: DbDep,
    admin: Annotated[User, Depends(require_admin)],
):
    """
    Admin clicks 'Reset Password' on a user. Returns a fresh reset URL
    with a 30-minute TTL. The admin shares this URL with the user out-of-band.
    
    Audit row: ADMIN_PASSWORD_RESET_INITIATED with the admin as actor.
    The subsequent PASSWORD_RESET_COMPLETED row will attribute the user.
    The two-row pair forms the complete forensic record.
    """
    target_user = repo.fetch_user_by_id(db, user_id)
    if target_user is None:
        raise HTTPException(status_code=404, detail="User not found.")

    ip = getattr(request.state, "client_ip", None)
    ua = getattr(request.state, "user_agent", None)

    try:
        raw_token, expires_at = auth_service.admin_initiate_password_reset(
            db,
            actor=admin,
            target_user=target_user,
            ip_address=ip,
            user_agent=ua,
        )
    except auth_service.InvalidResetTokenError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    reset_url = f"{frontend_base}/reset-password#token={raw_token}"

    return AdminInitiateResetResponse(
        user_id=target_user.id,
        email=target_user.email,
        reset_url=reset_url,
        expires_at=expires_at,
    )


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete a user (deactivate + anonymize email + revoke sessions).",
)
def delete_user(
    user_id: UUID,
    request: Request,
    db: DbDep,
    admin: Annotated[User, Depends(require_admin)],
):
    """
    Soft-deletes a user. The User row is preserved (audit FK integrity);
    the email is anonymized; all refresh tokens are revoked.
    
    Returns 204 on success — no body. The frontend should refresh the
    user list and show a toast like "User deleted."
    
    Returns 404 if the user_id doesn't exist.
    Returns 409 if the deletion would leave the system without an Admin
    or if the actor is trying to delete themselves.
    """
    target = repo.fetch_user_by_id(db, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found.")

    try:
        auth_service.admin_delete_user(
            db, actor=admin, target_user=target, request=request,
        )
    except LastAdminError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    return None  # 204 No Content