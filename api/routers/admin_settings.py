# api/routers/admin_settings.py
"""
Admin Global Settings — Tab 4.

Endpoints:
  GET /admin/settings
  GET /admin/settings/{key}
  PUT /admin/settings/{key}
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from api.dependencies.auth import DbDep, require_admin, require_analyst
from api.schemas import (
    GlobalSettingItem,
    GlobalSettingsListResponse,
    UpdateSettingRequest,
)
from domain.auth_models import AuditStatus, User
from repository import auth_repository as repo
from services import audit_service

router = APIRouter(prefix="/admin/settings", tags=["admin-settings"])


# ── Per-key validators ──────────────────────────────────────────────────────
#
# Each known key has ONE validator — a callable raising ValueError on bad
# input, returning the (possibly normalised) value on good input. New
# keys require: (a) a migration to seed the row, (b) an entry here. Both
# changes are reviewable in one PR.

def _validate_pct(v: Any) -> float:
    if not isinstance(v, (int, float)):
        raise ValueError("value must be a number")
    f = float(v)
    if not 0.0 <= f <= 100.0:
        raise ValueError("value must be between 0 and 100 (inclusive)")
    return f


def _validate_positive_int(v: Any) -> int:
    if not isinstance(v, int) or isinstance(v, bool):
        # Reject booleans — Python treats True as int(1) but it's a category error here.
        raise ValueError("value must be a non-negative integer")
    if v < 1:
        raise ValueError("value must be ≥ 1")
    return v


SETTING_VALIDATORS: dict[str, Any] = {
    "forecast_deviation_alert_pct": _validate_pct,
    "booking_volume_benchmark": _validate_positive_int,
    "default_date_range_weeks": _validate_positive_int,
}


def _validate_for_key(key: str, value: Any) -> Any:
    validator = SETTING_VALIDATORS.get(key)
    if validator is None:
        # Unknown keys are NEVER created via the API — see repo.upsert_setting.
        raise HTTPException(status_code=404, detail=f"Unknown setting key: {key!r}")
    try:
        return validator(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ─────────────────────────────────────────────────────────────────────────────


def _to_item(row) -> GlobalSettingItem:
    raw = row.value_json or {}
    if isinstance(raw, dict) and "value" in raw:
        unwrapped = raw["value"]
    else:
        unwrapped = raw
    return GlobalSettingItem(
        key=row.key,
        value=unwrapped,
        description=row.description,
        updated_at=row.updated_at,
        updated_by_user_id=row.updated_by_user_id,
    )


@router.get(
    "",
    response_model=GlobalSettingsListResponse,
    summary="List all global settings.",
)
def list_settings(
    db: DbDep,
    _user: Annotated[User, Depends(require_analyst)],  # readable by Analyst+
):
    rows = repo.list_settings(db)
    return GlobalSettingsListResponse(items=[_to_item(r) for r in rows])


@router.get(
    "/{key}",
    response_model=GlobalSettingItem,
    summary="Read a single setting by key.",
)
def get_setting(
    key: str,
    db: DbDep,
    _user: Annotated[User, Depends(require_analyst)],
):
    row = repo.fetch_setting(db, key)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Unknown setting key: {key!r}")
    return _to_item(row)


@router.put(
    "/{key}",
    response_model=GlobalSettingItem,
    summary="Update a setting. Per-key validation enforced.",
)
def update_setting(
    key: str,
    body: UpdateSettingRequest,
    request: Request,
    db: DbDep,
    admin: Annotated[User, Depends(require_admin)],
):
    # Per-key semantic validation (raises 400 / 404).
    normalized = _validate_for_key(key, body.value)

    existing = repo.fetch_setting(db, key)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Unknown setting key: {key!r}")

    old_value = existing.value_json
    repo.upsert_setting(
        db, key=key, value=normalized, updated_by_user_id=admin.id
    )

    audit_service.log_action(
        db,
        action_type="SETTINGS_UPDATED",
        status=AuditStatus.SUCCESS,
        actor=admin,
        target_resource={"settings_key": key},
        request=request,
        metadata={"old_value": old_value, "new_value": {"value": normalized}},
    )
    db.commit()
    db.refresh(existing)
    return _to_item(existing)