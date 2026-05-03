# repository/auth_repository.py
"""
Repository functions for the access-control domain.

This module is a thin layer over SQLAlchemy that returns ORM objects (or
None). It contains NO business logic — no policy decisions, no audit
writes, no password hashing. Those belong in services/auth_service.py.

Why a separate file from model_repository.py:
  Single-responsibility per repository module. The ML repository is
  focused on the SARIMAX/forecast domain; mixing user lookups into it
  would couple unrelated concerns and make code review harder. Two
  repositories, two domains, one shared SessionLocal.
  ISO 25010 → Maintainability → Modularity.

Function naming convention:
  fetch_*  → read-only, returns ORM object or None
  insert_* → INSERT only; caller is responsible for session.commit()
  update_* → mutation only; caller is responsible for session.commit()
  delete_* → DELETE only; caller is responsible for session.commit()

The "caller commits" rule is deliberate — atomicity needs to span MULTIPLE
repo calls (e.g., insert_user + insert_audit_log in one transaction during
invite consumption). Exposing commit() to the caller keeps that possible.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from domain.auth_models import (
    AuditLog,
    AuditStatus,
    InviteToken,
    RefreshToken,
    User,
    UserRole,
)


# ─────────────────────────────────────────────────────────────────────────────
# USER QUERIES
# ─────────────────────────────────────────────────────────────────────────────


def fetch_user_by_email(db: Session, email: str) -> Optional[User]:
    """
    Case-insensitive email lookup. Email is stored lowercased on creation
    (bootstrap_admin and the future admin-invite endpoint both lowercase),
    but we also lower() at lookup time for defense in depth — cheap.
    """
    return (
        db.query(User)
        .filter(func.lower(User.email) == email.strip().lower())
        .first()
    )


def fetch_user_by_id(db: Session, user_id: UUID | str) -> Optional[User]:
    """
    UUID lookup — used by the JWT-resolver dependency. Accepts str for the
    raw payload from `decode_token`, or UUID for type-safe call sites.
    """
    if isinstance(user_id, str):
        try:
            user_id = UUID(user_id)
        except ValueError:
            return None
    return db.query(User).filter(User.id == user_id).first()


def update_user_last_login(db: Session, user: User) -> None:
    """
    Bump the last_login_at column. Caller commits.
    Why use a fresh datetime here rather than a server-side now():
      The User row's `updated_at` has its own onupdate hook; touching
      last_login_at from Python keeps the two columns conceptually
      independent (last_login_at = "I authenticated", updated_at =
      "anyone touched this row"). Tiny, but defensible.
    """
    user.last_login_at = datetime.now(timezone.utc)


# ─────────────────────────────────────────────────────────────────────────────
# REFRESH TOKEN QUERIES
# ─────────────────────────────────────────────────────────────────────────────


def fetch_refresh_token_by_hash(
    db: Session, token_hash: str
) -> Optional[RefreshToken]:
    """
    Indexed lookup against refresh_tokens.token_hash. Returns the row
    even if it has been consumed — the auth_service is responsible for
    distinguishing "live" from "consumed" and triggering reuse-detection
    revocation.
    """
    return (
        db.query(RefreshToken)
        .filter(RefreshToken.token_hash == token_hash)
        .first()
    )


def insert_refresh_token(
    db: Session,
    *,
    user_id: UUID,
    token_hash: str,
    expires_at: datetime,
    user_agent: Optional[str] = None,
) -> RefreshToken:
    """
    Persist a new refresh-token row. Caller commits.
    """
    row = RefreshToken(
        user_id=user_id,
        token_hash=token_hash,
        expires_at=expires_at,
        user_agent=(user_agent or None),
    )
    db.add(row)
    db.flush()  # populate row.id without committing
    return row


def mark_refresh_token_consumed(
    db: Session,
    *,
    token_row: RefreshToken,
    replaced_by: Optional[RefreshToken] = None,
) -> None:
    """
    Atomically mark a refresh token as consumed (rotated or logged out).
    `replaced_by=None` means "logged out / forced revocation"; a value
    means "rotated forward, here is the successor."
    Caller commits.
    """
    token_row.consumed_at = datetime.now(timezone.utc)
    token_row.replaced_by_id = replaced_by.id if replaced_by else None


def revoke_all_refresh_tokens_for_user(db: Session, user_id: UUID) -> int:
    """
    Reuse-detection nuclear option. Returns the count of tokens revoked.
    Used when:
      - A consumed refresh token is presented again (replay attack signal)
      - An admin deactivates a user
      - A user requests global logout

    We DELETE rather than mark consumed, because revoked tokens have no
    forensic value beyond what the audit_log already captures.
    """
    deleted = (
        db.query(RefreshToken)
        .filter(RefreshToken.user_id == user_id)
        .delete(synchronize_session=False)
    )
    return int(deleted)


# ─────────────────────────────────────────────────────────────────────────────
# INVITE TOKEN QUERIES
# ─────────────────────────────────────────────────────────────────────────────


def fetch_invite_by_hash_for_update(
    db: Session, token_hash: str
) -> Optional[InviteToken]:
    """
    Look up an invite by SHA-256 hash with a row-level lock.

    The `with_for_update()` is LOAD-BEARING. Without it, two browser tabs
    submitting the same invite token simultaneously could both pass the
    consumed_at IS NULL check, both INSERT a User, and only one would
    successfully UPDATE the invite — but two Users would exist. The
    SELECT ... FOR UPDATE serializes the second tab's read until the
    first tab commits.

    On SQLite (local dev), with_for_update() is a no-op — SQLite's whole-DB
    write lock provides equivalent semantics for the sequential test cases
    we run locally. The protection only matters in concurrent Postgres,
    which is the production target.
    """
    return (
        db.query(InviteToken)
        .filter(InviteToken.token_hash == token_hash)
        .with_for_update()
        .one_or_none()
    )


def insert_invite_token(
    db: Session,
    *,
    token_hash: str,
    email: str,
    intended_role: UserRole,
    created_by_user_id: UUID,
    expires_at: datetime,
) -> InviteToken:
    """
    Persist a new pending invite row. Caller commits.
    """
    row = InviteToken(
        token_hash=token_hash,
        email=email.strip().lower(),
        intended_role=intended_role,
        created_by_user_id=created_by_user_id,
        expires_at=expires_at,
    )
    db.add(row)
    db.flush()
    return row


# ─────────────────────────────────────────────────────────────────────────────
# AUDIT LOG INSERT (the ONLY write surface for audit_logs)
# ─────────────────────────────────────────────────────────────────────────────


def insert_audit_log(
    db: Session,
    *,
    user_id: Optional[UUID],
    user_email_snapshot: Optional[str],
    action_type: str,
    module: str,
    target_resource: Optional[str] = None,
    status: AuditStatus,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    extra_metadata: Optional[dict] = None,
) -> AuditLog:
    """
    Append a single audit row. Caller commits.

    This is the ONLY function in the entire codebase that should INSERT
    into audit_logs. Phase 3 will wrap it in a higher-level
    services/audit_service.py that pins the action_type vocabulary at
    type-check time; until then, callers pass the literal string.

    NEVER add an UPDATE or DELETE function here. The audit log is
    append-only by ORGANIZATIONAL discipline; if you find yourself wanting
    to "fix" a row, you write a NEW row that supersedes the old one.
    """
    row = AuditLog(
        user_id=user_id,
        user_email_snapshot=user_email_snapshot,
        action_type=action_type,
        module=module,
        target_resource=target_resource,
        status=status,
        ip_address=ip_address,
        user_agent=(user_agent[:500] if user_agent else None),
        extra_metadata=extra_metadata,
    )
    db.add(row)
    db.flush()
    return row

# ═════════════════════════════════════════════════════════════════════════════
# PHASE 4 EXTENSIONS — admin queries.
# ═════════════════════════════════════════════════════════════════════════════
#
# These functions remain "caller commits" — we never call session.commit()
# inside the repository. The router/service that owns the request boundary
# is responsible for transaction lifecycle.

from sqlalchemy import or_, and_, func as sa_func, desc
from typing import Iterable
from datetime import datetime  # already imported above; harmless re-import


# ── USERS ───────────────────────────────────────────────────────────────────

def list_users_paginated(
    db: Session,
    *,
    page: int,
    page_size: int,
    role: Optional[UserRole] = None,
    is_active: Optional[bool] = None,
    search: Optional[str] = None,
) -> tuple[list[User], int]:
    """
    Returns (rows, total_count). Offset pagination — fine at <100K users.

    `search` matches against email OR full_name (case-insensitive).
    """
    q = db.query(User)
    if role is not None:
        q = q.filter(User.role == role)
    if is_active is not None:
        q = q.filter(User.is_active == is_active)
    if search:
        needle = f"%{search.strip().lower()}%"
        q = q.filter(or_(
            sa_func.lower(User.email).like(needle),
            sa_func.lower(User.full_name).like(needle),
        ))

    total = q.count()
    rows = (
        q.order_by(User.created_at.desc())
         .offset(max(page - 1, 0) * page_size)
         .limit(page_size)
         .all()
    )
    return rows, int(total)


def update_user_fields(
    db: Session,
    user: User,
    *,
    full_name: Optional[str] = None,
    role: Optional[UserRole] = None,
) -> User:
    """Mutates the user row in place. Caller commits."""
    if full_name is not None:
        user.full_name = full_name.strip()
    if role is not None:
        user.role = role
    return user


def set_user_active(db: Session, user: User, active: bool) -> User:
    """Caller commits."""
    user.is_active = active
    return user


def count_active_admins(db: Session) -> int:
    """Used by the 'last admin' guard in services/auth_service."""
    return int(
        db.query(sa_func.count(User.id))
        .filter(User.role == UserRole.ADMIN, User.is_active.is_(True))
        .scalar()
        or 0
    )


# ── INVITATIONS ─────────────────────────────────────────────────────────────

def list_invitations_paginated(
    db: Session,
    *,
    page: int,
    page_size: int,
    status_filter: Optional[str] = None,  # "pending" | "consumed" | "expired"
) -> tuple[list[InviteToken], int]:
    """
    `status_filter` interprets:
      "pending"   → consumed_at IS NULL AND expires_at > now()
      "consumed"  → consumed_at IS NOT NULL
      "expired"   → consumed_at IS NULL AND expires_at <= now()
      None        → all
    """
    now = datetime.now(timezone.utc)
    q = db.query(InviteToken)
    if status_filter == "pending":
        q = q.filter(and_(InviteToken.consumed_at.is_(None),
                          InviteToken.expires_at > now))
    elif status_filter == "consumed":
        q = q.filter(InviteToken.consumed_at.isnot(None))
    elif status_filter == "expired":
        q = q.filter(and_(InviteToken.consumed_at.is_(None),
                          InviteToken.expires_at <= now))

    total = q.count()
    rows = (
        q.order_by(InviteToken.created_at.desc())
         .offset(max(page - 1, 0) * page_size)
         .limit(page_size)
         .all()
    )
    return rows, int(total)


def fetch_invite_by_id(db: Session, invite_id: UUID | str) -> Optional[InviteToken]:
    if isinstance(invite_id, str):
        try:
            invite_id = UUID(invite_id)
        except ValueError:
            return None
    return db.query(InviteToken).filter(InviteToken.id == invite_id).first()


def fetch_pending_invite_for_email(
    db: Session, email: str
) -> Optional[InviteToken]:
    """Used by the 'no duplicate active invites' guard."""
    now = datetime.now(timezone.utc)
    return (
        db.query(InviteToken)
        .filter(
            sa_func.lower(InviteToken.email) == email.strip().lower(),
            InviteToken.consumed_at.is_(None),
            InviteToken.expires_at > now,
        )
        .first()
    )


def revoke_invite(db: Session, invite: InviteToken) -> InviteToken:
    """Sets expires_at = now() — equivalent to revocation, preserves the row.
    Caller commits."""
    invite.expires_at = datetime.now(timezone.utc)
    return invite


# ── AUDIT QUERY (cursor-based) ──────────────────────────────────────────────

def query_audit_logs(
    db: Session,
    *,
    limit: int,
    cursor_timestamp: Optional[datetime] = None,
    cursor_id: Optional[int] = None,
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
    action_type: Optional[str] = None,
    module: Optional[str] = None,
    status: Optional[AuditStatus] = None,
    user_id: Optional[UUID] = None,
) -> list[AuditLog]:
    """
    Cursor pagination on (timestamp DESC, id DESC). The cursor is a
    (timestamp, id) tuple — both required for stability when many rows
    share a timestamp (which they do on bursty events like a CSV upload
    that writes 5 rows in 50 ms).

    The query plan:
      WHERE (timestamp, id) < (cursor_ts, cursor_id)
      ORDER BY timestamp DESC, id DESC
      LIMIT N
    Postgres uses ix_audit_timestamp_desc + the PK btree; O(log n) seek
    to the cursor, then linear forward read of `limit` rows.
    """
    q = db.query(AuditLog)

    if cursor_timestamp is not None and cursor_id is not None:
        q = q.filter(
            or_(
                AuditLog.timestamp < cursor_timestamp,
                and_(
                    AuditLog.timestamp == cursor_timestamp,
                    AuditLog.id < cursor_id,
                ),
            )
        )

    if from_date is not None:
        q = q.filter(AuditLog.timestamp >= from_date)
    if to_date is not None:
        q = q.filter(AuditLog.timestamp <= to_date)
    if action_type:
        q = q.filter(AuditLog.action_type == action_type)
    if module:
        q = q.filter(AuditLog.module == module)
    if status is not None:
        q = q.filter(AuditLog.status == status)
    if user_id is not None:
        q = q.filter(AuditLog.user_id == user_id)

    return (
        q.order_by(desc(AuditLog.timestamp), desc(AuditLog.id))
         .limit(limit)
         .all()
    )


def fetch_audit_log_by_id(db: Session, log_id: int) -> Optional[AuditLog]:
    return db.query(AuditLog).filter(AuditLog.id == log_id).first()


def distinct_audit_action_types(db: Session) -> list[str]:
    """Powers GET /admin/audit-logs/action-types."""
    rows = (
        db.query(AuditLog.action_type)
        .distinct()
        .order_by(AuditLog.action_type)
        .all()
    )
    return [r[0] for r in rows]


def distinct_audit_modules(db: Session) -> list[str]:
    rows = (
        db.query(AuditLog.module)
        .distinct()
        .order_by(AuditLog.module)
        .all()
    )
    return [r[0] for r in rows]


# ── GLOBAL SETTINGS ─────────────────────────────────────────────────────────

def list_settings(db: Session) -> list:
    """Return list of GlobalSetting; the import lives below to avoid
    a top-level circular reference if domain.auth_models ever changes."""
    from domain.auth_models import GlobalSetting
    return db.query(GlobalSetting).order_by(GlobalSetting.key).all()


def fetch_setting(db: Session, key: str):
    from domain.auth_models import GlobalSetting
    return db.query(GlobalSetting).filter(GlobalSetting.key == key).first()


def upsert_setting(
    db: Session,
    *,
    key: str,
    value: Any,
    description: Optional[str] = None,
    updated_by_user_id: Optional[UUID] = None,
):
    """
    Update existing setting OR raise. Phase 4 does NOT create new keys
    via the API — keys are seeded by the migration. This guard is the
    enforcement.

    Caller commits.
    """
    from domain.auth_models import GlobalSetting

    row = db.query(GlobalSetting).filter(GlobalSetting.key == key).first()
    if row is None:
        raise KeyError(f"Unknown setting key: {key!r}")

    row.value_json = {"value": value}
    if description is not None:
        row.description = description
    row.updated_by_user_id = updated_by_user_id
    return row


# ── SYSTEM OVERVIEW HELPERS ─────────────────────────────────────────────────

def count_active_users(db: Session) -> int:
    return int(
        db.query(sa_func.count(User.id))
        .filter(User.is_active.is_(True))
        .scalar()
        or 0
    )


def count_total_users(db: Session) -> int:
    return int(db.query(sa_func.count(User.id)).scalar() or 0)


def count_pending_invitations(db: Session) -> int:
    now = datetime.now(timezone.utc)
    return int(
        db.query(sa_func.count(InviteToken.id))
        .filter(
            InviteToken.consumed_at.is_(None),
            InviteToken.expires_at > now,
        )
        .scalar()
        or 0
    )


def fetch_recent_audit_rows(db: Session, limit: int = 20) -> list[AuditLog]:
    return (
        db.query(AuditLog)
        .order_by(desc(AuditLog.timestamp), desc(AuditLog.id))
        .limit(limit)
        .all()
    )

# ─────────────────────────────────────────────────────────────────────────────
# PASSWORD RESET QUERIES
# ─────────────────────────────────────────────────────────────────────────────


def insert_password_reset_token(
    db: Session,
    *,
    user_id: UUID,
    token_hash: str,
    expires_at: datetime,
    initiated_by_user_id: Optional[UUID] = None,
    ip_address: Optional[str] = None,
):
    """Caller commits."""
    from domain.auth_models import PasswordResetToken
    row = PasswordResetToken(
        user_id=user_id,
        token_hash=token_hash,
        expires_at=expires_at,
        initiated_by_user_id=initiated_by_user_id,
        ip_address_initiated=ip_address,
    )
    db.add(row)
    db.flush()
    return row


def fetch_password_reset_by_hash_for_update(
    db: Session, token_hash: str
):
    """SELECT ... FOR UPDATE. See fetch_invite_by_hash_for_update for rationale."""
    from domain.auth_models import PasswordResetToken
    return (
        db.query(PasswordResetToken)
        .filter(PasswordResetToken.token_hash == token_hash)
        .with_for_update()
        .one_or_none()
    )


def fetch_pending_reset_for_user(db: Session, user_id: UUID):
    """Used for the rate-limit guard: max one active reset per user."""
    from domain.auth_models import PasswordResetToken
    now = datetime.now(timezone.utc)
    return (
        db.query(PasswordResetToken)
        .filter(
            PasswordResetToken.user_id == user_id,
            PasswordResetToken.consumed_at.is_(None),
            PasswordResetToken.expires_at > now,
        )
        .first()
    )


def revoke_pending_resets_for_user(db: Session, user_id: UUID) -> int:
    """When admin initiates a fresh reset, expire any pending self-service ones.
    Returns count revoked. Caller commits."""
    from domain.auth_models import PasswordResetToken
    now = datetime.now(timezone.utc)
    count = (
        db.query(PasswordResetToken)
        .filter(
            PasswordResetToken.user_id == user_id,
            PasswordResetToken.consumed_at.is_(None),
            PasswordResetToken.expires_at > now,
        )
        .update({"expires_at": now}, synchronize_session=False)
    )
    return int(count)