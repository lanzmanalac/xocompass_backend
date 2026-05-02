# api/routers/admin_invitations.py
"""
Admin Invitations — Tab 1, Card 2.

Endpoints:
  POST   /admin/invitations
  GET    /admin/invitations
  DELETE /admin/invitations/{invite_id}

All endpoints require Admin role.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from api.dependencies.auth import DbDep, require_admin
from api.schemas import (
    CreateInvitationRequest,
    CreateInvitationResponse,
    InvitationListItem,
    InvitationListResponse,
)
from domain.auth_models import InviteToken, User, UserRole
from repository import auth_repository as repo
from services import auth_service
from services.auth_service import (
    DuplicateInviteError,
    EmailAlreadyExistsError,
)

router = APIRouter(prefix="/admin/invitations", tags=["admin-invitations"])


def _frontend_base_url() -> str:
    """Read at request time so a misconfigured deploy is recoverable
    without a restart. Phase 0 already ships FRONTEND_BASE_URL."""
    base = (os.getenv("FRONTEND_BASE_URL") or "").rstrip("/")
    if not base:
        # Fail safely — without a base URL we cannot return a usable invite_url.
        raise HTTPException(
            status_code=500,
            detail="FRONTEND_BASE_URL is not configured.",
        )
    return base


def _compute_status(invite: InviteToken) -> str:
    if invite.consumed_at is not None:
        return "consumed"
    now = datetime.now(timezone.utc)
    if invite.expires_at <= now:
        return "expired"
    return "pending"


def _to_list_item(i: InviteToken) -> InvitationListItem:
    return InvitationListItem(
        id=i.id,
        email=i.email,
        intended_role=i.intended_role.value,
        created_by_user_id=i.created_by_user_id,
        created_at=i.created_at,
        expires_at=i.expires_at,
        consumed_at=i.consumed_at,
        consumed_by_user_id=i.consumed_by_user_id,
        status=_compute_status(i),
    )


# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "",
    response_model=CreateInvitationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Issue a new invitation. Plaintext token is returned exactly once.",
)
def create_invitation(
    body: CreateInvitationRequest,
    request: Request,
    db: DbDep,
    admin: Annotated[User, Depends(require_admin)],
):
    try:
        invite, raw_token = auth_service.admin_issue_invite(
            db,
            actor=admin,
            email=body.email,
            intended_role=UserRole(body.role),
            request=request,
        )
    except EmailAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except DuplicateInviteError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    invite_url = f"{_frontend_base_url()}/register?token={raw_token}"
    return CreateInvitationResponse(
        invite_id=invite.id,
        email=invite.email,
        role=invite.intended_role.value,
        invite_url=invite_url,
        expires_at=invite.expires_at,
    )


@router.get(
    "",
    response_model=InvitationListResponse,
    summary="List invitations with optional status filter.",
)
def list_invitations(
    db: DbDep,
    _admin: Annotated[User, Depends(require_admin)],
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    status_filter: Optional[str] = Query(None, alias="status",
                                          pattern="^(pending|consumed|expired)$"),
):
    rows, total = repo.list_invitations_paginated(
        db, page=page, page_size=page_size, status_filter=status_filter,
    )
    return InvitationListResponse(
        items=[_to_list_item(i) for i in rows],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.delete(
    "/{invite_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a pending invitation. Idempotent.",
)
def revoke_invitation(
    invite_id: UUID,
    request: Request,
    db: DbDep,
    admin: Annotated[User, Depends(require_admin)],
):
    invite = repo.fetch_invite_by_id(db, invite_id)
    if invite is None:
        raise HTTPException(status_code=404, detail="Invitation not found.")

    auth_service.admin_revoke_invite(
        db, actor=admin, invite=invite, request=request,
    )
    return None  # 204 No Content