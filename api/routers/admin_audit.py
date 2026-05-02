# api/routers/admin_audit.py
"""
Admin Audit Log — Tab 3.

Endpoints:
  GET /admin/audit-logs
  GET /admin/audit-logs/{log_id}
  GET /admin/audit-logs/action-types

All endpoints require Admin role.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.dependencies.auth import DbDep, require_admin
from api.schemas import (
    AuditActionTypesResponse,
    AuditLogItem,
    AuditLogPageResponse,
)
from domain.auth_models import AuditLog, AuditStatus, User
from repository import auth_repository as repo
from services import audit_service
from services.audit_vocab import ALL_ACTION_TYPES, ALL_MODULES

router = APIRouter(prefix="/admin/audit-logs", tags=["admin-audit"])


def _to_item(row: AuditLog) -> AuditLogItem:
    return AuditLogItem(
        id=row.id,
        timestamp=row.timestamp,
        user_id=row.user_id,
        user_email_snapshot=row.user_email_snapshot,
        action_type=row.action_type,
        module=row.module,
        target_resource=row.target_resource,
        status=row.status.value,
        ip_address=str(row.ip_address) if row.ip_address is not None else None,
        user_agent=row.user_agent,
        extra_metadata=row.extra_metadata,
    )


# IMPORTANT: declare /action-types BEFORE /{log_id}, otherwise the path
# converter swallows it. FastAPI route resolution is order-dependent.

@router.get(
    "/action-types",
    response_model=AuditActionTypesResponse,
    summary="Enumerate vocabularies for the admin filter dropdowns.",
)
def get_audit_vocabularies(
    _admin: Annotated[User, Depends(require_admin)],
):
    """
    Returns the FULL controlled vocabulary, not just the values currently
    observed in audit_logs. The frontend's filter dropdown should let
    admins query for action types that haven't happened yet (e.g.,
    'show me TOKEN_REPLAY_DETECTED rows' on a fresh deploy with zero
    such rows). Sourced from services.audit_vocab.
    """
    return AuditActionTypesResponse(
        action_types=sorted(ALL_ACTION_TYPES),
        modules=sorted(ALL_MODULES),
    )


@router.get(
    "",
    response_model=AuditLogPageResponse,
    summary="Cursor-paginated audit log with optional filters.",
)
def list_audit_logs(
    db: DbDep,
    _admin: Annotated[User, Depends(require_admin)],
    cursor: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    from_date: Optional[datetime] = Query(None),
    to_date: Optional[datetime] = Query(None),
    action_type: Optional[str] = Query(None, max_length=64),
    module: Optional[str] = Query(None, max_length=32),
    status_filter: Optional[str] = Query(
        None, alias="status", pattern="^(SUCCESS|FAILED)$"
    ),
    user_id: Optional[UUID] = Query(None),
):
    status_enum: Optional[AuditStatus] = None
    if status_filter:
        status_enum = AuditStatus(status_filter)

    rows, next_cursor = audit_service.query_logs(
        db,
        limit=limit,
        cursor=cursor,
        from_date=from_date,
        to_date=to_date,
        action_type=action_type,
        module=module,
        status=status_enum,
        user_id=user_id,
    )
    return AuditLogPageResponse(
        items=[_to_item(r) for r in rows],
        next_cursor=next_cursor,
    )


@router.get(
    "/{log_id}",
    response_model=AuditLogItem,
    summary="Single audit row detail (full extra_metadata).",
)
def get_audit_log(
    log_id: int,
    db: DbDep,
    _admin: Annotated[User, Depends(require_admin)],
):
    row = repo.fetch_audit_log_by_id(db, log_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Audit log not found.")
    return _to_item(row)