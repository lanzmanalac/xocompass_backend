# api/routers/admin_system.py
"""
Admin System Overview — Tab 2.

Endpoints:
  GET /admin/system/overview
  GET /admin/system/pipeline-status
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, status as status_codes
from sqlalchemy import desc

from api.dependencies.auth import DbDep, require_admin, require_analyst
from api.schemas import (
    PipelineStatusResponse,
    RecentActivityItem,
    SystemOverviewResponse,
)
from domain.auth_models import AuditLog, AuditStatus, User
from domain.models import SarimaxModel, DatasetSnapshot
from repository import auth_repository as repo

router = APIRouter(prefix="/admin/system", tags=["admin-system"])


def _to_recent_activity(row: AuditLog) -> RecentActivityItem:
    return RecentActivityItem(
        id=row.id,
        timestamp=row.timestamp,
        actor_email=row.user_email_snapshot,
        action_type=row.action_type,
        module=row.module,
        status=row.status.value,
        target_resource=row.target_resource,
    )


def _pipeline_status(
    last_run_at: Optional[datetime],
    last_status: Optional[str],
) -> str:
    """
    healthy: a successful retrain in the last 14 days
    stale:   a successful retrain older than 14 days OR last attempt failed
    unknown: no retrains yet
    """
    if last_run_at is None or last_status is None:
        return "unknown"
    if last_status != "SUCCESS":
        return "stale"
    if last_run_at < datetime.now(timezone.utc) - timedelta(days=14):
        return "stale"
    return "healthy"


@router.get(
    "/overview",
    response_model=SystemOverviewResponse,
    summary="High-level platform health for the admin dashboard.",
)
def system_overview(
    db: DbDep,
    _admin: Annotated[User, Depends(require_admin)],
):
    active_users = repo.count_active_users(db)
    total_users = repo.count_total_users(db)
    pending_invites = repo.count_pending_invitations(db)

    # Last successful CSV ingestion — derived from the latest DatasetSnapshot.
    last_snapshot: Optional[DatasetSnapshot] = (
        db.query(DatasetSnapshot)
        .order_by(DatasetSnapshot.generated_at.desc())
        .first()
    )

    # Last forecast run — derived from the latest active model's created_at.
    last_active: Optional[SarimaxModel] = (
        db.query(SarimaxModel)
        .filter(SarimaxModel.is_active.is_(True))
        .order_by(SarimaxModel.created_at.desc())
        .first()
    )

    last_forecast_audit: Optional[AuditLog] = (
        db.query(AuditLog)
        .filter(AuditLog.action_type.in_(["FORECAST_RUN", "FORECAST_FAILED"]))
        .order_by(desc(AuditLog.timestamp), desc(AuditLog.id))
        .first()
    )

    pipeline = _pipeline_status(
        last_run_at=last_forecast_audit.timestamp if last_forecast_audit else None,
        last_status=last_forecast_audit.action_type
        and (
            "SUCCESS"
            if last_forecast_audit.action_type == "FORECAST_RUN"
            else "FAILED"
        )
        if last_forecast_audit else None,
    )

    recent_rows = repo.fetch_recent_audit_rows(db, limit=20)

    return SystemOverviewResponse(
        active_users_count=active_users,
        total_users_count=total_users,
        pending_invitations_count=pending_invites,
        last_data_sync=last_snapshot.generated_at if last_snapshot else None,
        last_data_sync_records=(
            last_snapshot.total_weekly_records if last_snapshot else None
        ),
        last_forecast_run_at=last_active.created_at if last_active else None,
        last_forecast_model_id=last_active.id if last_active else None,
        pipeline_status=pipeline,
        recent_activity=[_to_recent_activity(r) for r in recent_rows],
    )


@router.get(
    "/pipeline-status",
    response_model=PipelineStatusResponse,
    summary="Lightweight pipeline status (Admin or Analyst).",
)
def pipeline_status(
    db: DbDep,
    _user: Annotated[User, Depends(require_analyst)],
):
    last_audit: Optional[AuditLog] = (
        db.query(AuditLog)
        .filter(AuditLog.action_type.in_(["FORECAST_RUN", "FORECAST_FAILED"]))
        .order_by(desc(AuditLog.timestamp), desc(AuditLog.id))
        .first()
    )

    if last_audit is None:
        return PipelineStatusResponse(last_status="NEVER_RUN")

    last_active = (
        db.query(SarimaxModel)
        .filter(SarimaxModel.is_active.is_(True))
        .order_by(SarimaxModel.created_at.desc())
        .first()
    )

    return PipelineStatusResponse(
        last_run_at=last_audit.timestamp,
        last_model_id=last_active.id if last_active else None,
        last_status=(
            "SUCCESS" if last_audit.action_type == "FORECAST_RUN" else "FAILED"
        ),
    )