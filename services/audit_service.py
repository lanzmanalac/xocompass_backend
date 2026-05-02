# services/audit_service.py
"""
Typed facade over repository.auth_repository.insert_audit_log.

USAGE:
    from services import audit_service
    from services.audit_vocab import DEFAULT_MODULE   # rarely needed directly

    audit_service.log_action(
        db,
        action_type="DATA_UPLOADED",     # Literal — typo'd values fail typecheck
        actor=current_user,              # may be None for unauthenticated calls
        target_resource={"upload_filename": "kjs_2025.csv", "rows": 412},
        status=AuditStatus.SUCCESS,
        request=request,                 # FastAPI Request — for IP/UA extraction
        metadata={"records_ingested": 412},
    )

DESIGN INVARIANTS:
  1. NEVER call session.commit() inside this function. The CALLER owns
     the transaction boundary. Audit writes are part of the action's
     transaction — if the action's logic fails, the audit row rolls
     back too. ISO 25010 → Reliability → Fault Tolerance.

  2. NEVER raise on internal failures. An audit-write failure must NOT
     propagate up to mask the real outcome of the action. We log the
     internal failure to the application logger and return None.
     Rationale: a forecast retrain that succeeded must not appear to
     the user as "500 internal server error" because the audit subsystem
     hiccupped. ISO 25010 → Reliability → Maturity (graceful degradation).

  3. The action_type parameter is typed `ActionTypeLiteral` — see
     services/audit_vocab.py for the full vocabulary. Adding a new
     action requires editing the vocab file FIRST, which is the
     desired friction.

  4. `actor` may be None. Phase 3 wires four endpoints that are still
     publicly callable; their audit rows pass actor=None and the rows
     are written with user_id=NULL + a `unauthenticated: true` marker
     in extra_metadata. Phase 5's RBAC retrofit replaces those Nones
     with the resolved User. The marker survives — it tells the
     forensic reader "this row predates RBAC enforcement."
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional

from fastapi import Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from domain.auth_models import AuditLog, AuditStatus, User
from repository import auth_repository as repo
from services.audit_vocab import ActionTypeLiteral, DEFAULT_MODULE, ModuleLiteral

logger = logging.getLogger(__name__)

# audit_logs.target_resource is VARCHAR(120). Truncate, don't crash.
_TARGET_RESOURCE_MAX = 120
# audit_logs.user_agent is VARCHAR(500). insert_audit_log already truncates,
# but we mirror the limit here for consistency.
_USER_AGENT_MAX = 500


# ═════════════════════════════════════════════════════════════════════════════
# Public API
# ═════════════════════════════════════════════════════════════════════════════


def log_action(
    db: Session,
    *,
    action_type: ActionTypeLiteral,
    status: AuditStatus,
    actor: Optional[User] = None,
    actor_email_override: Optional[str] = None,
    module: Optional[ModuleLiteral] = None,
    target_resource: Optional[str | Mapping[str, Any]] = None,
    metadata: Optional[Mapping[str, Any]] = None,
    request: Optional[Request] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> Optional[AuditLog]:
    """
    Append an audit row. Returns the staged ORM object (caller commits)
    or None if the audit subsystem failed internally (we never re-raise
    — the action's outcome must not depend on audit infrastructure).

    Parameters
    ----------
    db
        Active session, inside an open transaction.
    action_type
        One of services.audit_vocab.ActionTypeLiteral. Type-checked.
    status
        AuditStatus.SUCCESS or AuditStatus.FAILED.
    actor
        The User who performed the action. Pass None for unauthenticated
        calls (Phase 3 endpoints) or LOGIN_FAILED with no resolved user.
    actor_email_override
        Used only when actor is None but you DO know the email someone
        attempted (e.g., LOGIN_FAILED has the attempted email but no
        User row). Populates user_email_snapshot.
    module
        Override the default module resolution. Almost never needed —
        DEFAULT_MODULE[action_type] is the right answer. Pass explicitly
        only for cross-cutting actions like USER_CREATED via bootstrap
        (module="bootstrap" instead of "user_management").
    target_resource
        Free-form identifier of WHAT was acted on. If a Mapping, it's
        rendered as `key1=value1; key2=value2` and truncated to 120 chars.
        If a str, used verbatim (still truncated).
    metadata
        Goes into extra_metadata as a JSON object. Use it for typed
        context (record counts, before/after values, failure reasons).
    request
        FastAPI Request — if provided, ip_address and user_agent are
        extracted from it. Explicit ip_address/user_agent kwargs win
        if both are given (useful for tests).
    """
    # ── 1. Resolve module ──────────────────────────────────────────────────
    resolved_module = module or DEFAULT_MODULE.get(action_type)
    if resolved_module is None:
        # Defensive: should never happen given the boot-time sanity check
        # in audit_vocab.py, but if we somehow ship a typed action without
        # a default, fail safely with a logged warning rather than letting
        # NULL hit the NOT NULL column.
        logger.warning(
            "audit_service: no default module for action_type=%s; using 'auth' fallback.",
            action_type,
        )
        resolved_module = "auth"

    # ── 2. Resolve actor identity ──────────────────────────────────────────
    user_id = actor.id if actor is not None else None
    if actor is not None:
        email_snapshot = actor.email
    elif actor_email_override is not None:
        email_snapshot = actor_email_override.strip().lower()
    else:
        email_snapshot = None

    # ── 3. Resolve request context ─────────────────────────────────────────
    final_ip = ip_address
    final_ua = user_agent
    if request is not None:
        if final_ip is None:
            # api/dependencies/auth.get_current_user already stashes
            # request.state.client_ip when an authenticated dependency
            # has run. Prefer that; fall back to header parsing for
            # unauthenticated endpoints.
            final_ip = getattr(request.state, "client_ip", None) or _client_ip(request)
        if final_ua is None:
            final_ua = (
                getattr(request.state, "user_agent", None)
                or request.headers.get("user-agent")
            )
    if final_ua is not None:
        final_ua = final_ua[:_USER_AGENT_MAX]

    # ── 4. Render target_resource ──────────────────────────────────────────
    rendered_target = _render_target_resource(target_resource)

    # ── 5. Augment metadata for unauthenticated calls ──────────────────────
    # Phase 3 endpoints don't have an authenticated actor yet. Mark the
    # row so Phase 5's diff cleanly distinguishes pre/post-RBAC events.
    final_metadata: dict[str, Any] = dict(metadata) if metadata else {}
    if actor is None and action_type not in _UNAUTHENTICATED_BY_DESIGN:
        final_metadata.setdefault("unauthenticated", True)

    # ── 6. Append. Never re-raise. ─────────────────────────────────────────
    try:
        return repo.insert_audit_log(
            db,
            user_id=user_id,
            user_email_snapshot=email_snapshot,
            action_type=action_type,
            module=resolved_module,
            target_resource=rendered_target,
            status=status,
            ip_address=final_ip,
            user_agent=final_ua,
            extra_metadata=final_metadata or None,
        )
    except SQLAlchemyError:
        # The action's primary write may still succeed; we must not let
        # the audit subsystem mask it. Log loudly and continue.
        logger.exception(
            "audit_service: failed to append audit row "
            "(action_type=%s, status=%s, user_id=%s).",
            action_type, status.value, user_id,
        )
        return None


# ═════════════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════════════


# Action types that are SUPPOSED to be unauthenticated (LOGIN_FAILED for
# unknown emails, INVITE_CONSUMED before the User exists). These do NOT get
# the `unauthenticated: true` marker — that flag is reserved for endpoints
# that *should* be authenticated but aren't yet (Phase 3 → Phase 5).
_UNAUTHENTICATED_BY_DESIGN = frozenset({
    "LOGIN",            # the actor IS the user; we set actor=user explicitly
    "LOGIN_FAILED",
    "LOGOUT",
    "TOKEN_REFRESHED",
    "TOKEN_REPLAY_DETECTED",
    "INVITE_CONSUMED",
    "USER_CREATED",     # bootstrap or invite consumption — actor=new_user
})


def _client_ip(request: Request) -> Optional[str]:
    """Same logic as api.dependencies.auth._client_ip — duplicated here
    deliberately so audit_service has zero dependency on the auth
    dependency module. ISO 25010 → Maintainability → Modularity."""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip() or None
    return request.client.host if request.client else None


def _render_target_resource(
    raw: Optional[str | Mapping[str, Any]],
) -> Optional[str]:
    """Render a target_resource value to a fixed-width string.

    str           → used verbatim (truncated)
    Mapping       → "k1=v1; k2=v2" (truncated)
    None          → None
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw[:_TARGET_RESOURCE_MAX]
    if isinstance(raw, Mapping):
        rendered = "; ".join(f"{k}={v}" for k, v in raw.items())
        return rendered[:_TARGET_RESOURCE_MAX]
    # Defensive: unknown type — stringify and truncate.
    return str(raw)[:_TARGET_RESOURCE_MAX]


__all__ = ["log_action"]

# ═════════════════════════════════════════════════════════════════════════════
# PHASE 4 — Audit query facade (READ-ONLY).
#
# This module already owns log_action (the only write surface). Adding the
# read facade here keeps the audit subsystem cohesive: one module is the
# entire boundary between application code and audit_logs. Phase 4 routers
# never query audit_logs directly.
# ═════════════════════════════════════════════════════════════════════════════

import base64
import json
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from domain.auth_models import AuditLog, AuditStatus
from repository import auth_repository as auth_repo


# ── Cursor encode/decode ────────────────────────────────────────────────────


def encode_cursor(timestamp: datetime, log_id: int) -> str:
    """
    Opaque cursor. The frontend treats the string as an opaque token.
    base64(json) — easy to debug, no PII exposure (timestamps are
    public-safe), easy to extend with extra fields later.
    """
    payload = {"ts": timestamp.isoformat(), "id": log_id}
    return base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")


def decode_cursor(cursor: str) -> Optional[tuple[datetime, int]]:
    """Returns (timestamp, id) or None if the cursor is malformed."""
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        obj = json.loads(raw)
        ts = datetime.fromisoformat(obj["ts"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts, int(obj["id"])
    except Exception:
        return None


# ── Public read API ─────────────────────────────────────────────────────────


def query_logs(
    db,
    *,
    limit: int,
    cursor: Optional[str] = None,
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
    action_type: Optional[str] = None,
    module: Optional[str] = None,
    status: Optional[AuditStatus] = None,
    user_id: Optional[UUID] = None,
) -> tuple[list[AuditLog], Optional[str]]:
    """
    Returns (rows, next_cursor). Rows are ordered (timestamp DESC, id DESC).

    Parameters
    ----------
    limit
        Number of rows to return; the function fetches limit+1 internally
        to detect whether more exist (for next_cursor) without a count.
    cursor
        Opaque cursor from a previous response. None on the first page.
    from_date / to_date
        Inclusive date bounds.
    action_type / module / status / user_id
        Optional filters.
    """
    cursor_ts: Optional[datetime] = None
    cursor_id: Optional[int] = None
    if cursor:
        decoded = decode_cursor(cursor)
        if decoded is not None:
            cursor_ts, cursor_id = decoded
        # If decode fails, treat as no cursor — first page semantics.

    # Fetch one more than requested so we can detect "is there a next page?"
    rows = auth_repo.query_audit_logs(
        db,
        limit=limit + 1,
        cursor_timestamp=cursor_ts,
        cursor_id=cursor_id,
        from_date=from_date,
        to_date=to_date,
        action_type=action_type,
        module=module,
        status=status,
        user_id=user_id,
    )

    next_cursor: Optional[str] = None
    if len(rows) > limit:
        # The (limit+1)-th row is our cursor anchor.
        last_visible = rows[limit - 1]
        next_cursor = encode_cursor(last_visible.timestamp, last_visible.id)
        rows = rows[:limit]

    return rows, next_cursor