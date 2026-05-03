# domain/auth_models.py
"""
Access-control & audit persistence models.

This module is intentionally separate from `domain/models.py` (which holds
the ML/forecasting domain). Two domains, two files. Reasons:

  1. ISO 25010 → Maintainability → Modularity. The auth domain has zero
     business-logic dependency on the ML domain and vice versa. Keeping
     them in separate files prevents a careless edit in one from forcing
     a re-import or migration of the other.

  2. ISO 25010 → Maintainability → Modifiability. If KJS ever swaps the
     auth implementation (e.g., delegates to Google IAP or Cognito), the
     entire access-control surface deletes cleanly along this seam.

DESIGN INVARIANTS:
  - Both modules MUST share the same `Base = DeclarativeBase` from
    `domain/models.py`. Two `Base` instances would produce two metadata
    registries and Alembic autogenerate would silently miss tables.
  - All timestamps use `DateTime(timezone=True)` and the existing
    `get_ph_now` default. Storage convention is GMT+8 across the platform;
    JWT timestamps (UTC) are converted at the API boundary, not here.
  - All primary keys for human-facing tables (User, InviteToken,
    RefreshToken) are UUIDs. AuditLog uses BigInteger for monotonically
    increasing inserts at append-only volume.

THIS MODULE DOES NOT IMPORT FROM core/, api/, services/, OR repository/.
It is a leaf — only `domain/models.py` (for Base + get_ph_now) and SQLAlchemy
itself.
"""

from __future__ import annotations

import enum
import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    JSON,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import INET, UUID
from sqlalchemy.orm import relationship

# Reuse the SAME declarative Base and timestamp helper as the ML domain.
# This is non-negotiable — see the docstring above.
from domain.models import Base, get_ph_now


# ═════════════════════════════════════════════════════════════════════════════
# ENUMS — closed sets, mapped to PostgreSQL ENUM types.
# ═════════════════════════════════════════════════════════════════════════════
#
# Why Python Enum + SQLAlchemy SAEnum (not a lookup table):
#   - Roles and audit statuses are CLOSED sets, frozen at design time. Adding
#     a 4th role would require code changes regardless of whether they live
#     in a table or an enum, because the *behavior* lives in dependency code
#     (Phase 2 RBAC), not in a row.
#   - SAEnum maps to a real PostgreSQL ENUM type — DB-level integrity. An
#     INSERT with `role='HACKER'` is rejected by Postgres, not by Python.
#   - No JOIN required to render the role at read time.
#
# WARNING for downgrade authors:
#   PostgreSQL ENUM types are NOT auto-dropped when their last referencing
#   table is dropped. The Alembic downgrade in §1.3 must explicitly call
#   `op.execute("DROP TYPE user_role")` and `DROP TYPE audit_status`. This is
#   a known Alembic autogenerate gap; we handle it manually.
# ═════════════════════════════════════════════════════════════════════════════


class UserRole(str, enum.Enum):
    """
    The complete access gradient.

    ADMIN    — write everything, including users, invitations, settings.
    ANALYST  — write models/data; read-only on users/invitations/audit.
    VIEWER   — read-only on dashboards and forecasts; no writes anywhere.

    Inheriting from `str` makes the value JSON-serializable as the bare
    string ("ADMIN", not "UserRole.ADMIN") without a custom encoder.
    """

    ADMIN = "ADMIN"
    ANALYST = "ANALYST"
    VIEWER = "VIEWER"


class AuditStatus(str, enum.Enum):
    """
    Outcome of an auditable action.

    SUCCESS — the action committed.
    FAILED  — the action was attempted and rejected (e.g., bad password,
              forbidden role, validation error). Failed-login telemetry
              lives entirely in this status.
    """

    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


# Module-level instances reused by every column declaration below. Defining
# them once with explicit `name=` ensures the PostgreSQL ENUM type name is
# stable and identical wherever the column is referenced — Alembic compares
# by name, so this prevents spurious "type changed" diffs on future autogens.
_USER_ROLE_ENUM = SAEnum(UserRole, name="user_role")
_AUDIT_STATUS_ENUM = SAEnum(AuditStatus, name="audit_status")


# ═════════════════════════════════════════════════════════════════════════════
# 1. USER — the principal that holds a role and authenticates.
# ═════════════════════════════════════════════════════════════════════════════

class User(Base):
    """
    A human principal. Provisioned by an Admin via the invite flow; the row
    is INSERTed at registration time, not at invitation time, so there is
    never a "ghost user" with no password sitting around as an attack target.

    Why UUID over auto-increment integer:
      - Enumeration resistance. /admin/users/47 leaks "we have <50 users"
        and lets attackers probe sequentially. /admin/users/3f29... leaks
        nothing.
      - Cross-environment migration safety. Copying a row from staging to
        prod requires no key remapping.
      - Negligible cost: 16 bytes vs 4, slightly slower btree. At KJS scale
        (≤100 users) the difference is unmeasurable.

    Why `hashed_password` is NULLABLE:
      - The User row is created at registration time, AFTER an invite is
        consumed. The hash is set in the same transaction; the column is
        nullable only to permit a forward-compatible flow where an admin
        could pre-create a user without a password (e.g., for SSO migration
        later). v1 always sets it on INSERT — Phase 2 service layer enforces.
    """

    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    email = Column(String(255), unique=True, nullable=False, index=True)
    full_name = Column(String(120), nullable=False)

    # Argon2id PHC string from core.security.hash_password().
    # Format: $argon2id$v=19$m=...,t=...,p=...$<salt>$<hash>
    # ~96 chars typical. 255 leaves headroom for future cost-param growth.
    hashed_password = Column(String(255), nullable=True)

    role = Column(_USER_ROLE_ENUM, nullable=False)

    # is_active is the SOFT-DELETE / DEACTIVATION flag. We never DELETE users
    # — that would break audit referential integrity. We flip is_active=false
    # and revoke their refresh tokens. Deactivated users keep their audit
    # history under their original UUID + email_snapshot.
    is_active = Column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    last_login_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=get_ph_now, nullable=False)
    updated_at = Column(
        DateTime(timezone=True), default=get_ph_now, onupdate=get_ph_now, nullable=False
    )

    # Self-referential FK — provenance. NULL for the bootstrap admin (no one
    # provisioned them). Every subsequent user has a non-NULL value. This is
    # forensic gold during a security incident: "who provisioned the
    # compromised account?"
    created_by_user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )

    # Relationships ----------------------------------------------------------
    # remote_side disambiguates the self-join for SQLAlchemy.
    created_by = relationship(
        "User", remote_side=[id], foreign_keys=[created_by_user_id]
    )

    # Invites this user has ISSUED (as an admin).
    invites_issued = relationship(
        "InviteToken",
        back_populates="created_by",
        foreign_keys="InviteToken.created_by_user_id",
    )

    # Refresh tokens this user currently holds. Cascade delete is correct
    # here: if a user row is ever hard-deleted (which we don't do, but
    # defensively), their refresh tokens go too.
    refresh_tokens = relationship(
        "RefreshToken",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        # Deliberately omits hashed_password — never log it.
        return f"<User id={self.id} email={self.email!r} role={self.role.value} active={self.is_active}>"


# ═════════════════════════════════════════════════════════════════════════════
# 2. INVITE TOKEN — single-use, hashed-at-rest, time-bound.
# ═════════════════════════════════════════════════════════════════════════════

class InviteToken(Base):
    """
    A pending or consumed invitation. The plaintext token never touches
    this table — only its SHA-256 hex digest. See core.security for the
    threat model rationale.

    Lifecycle:
      created  -> consumed_at=NULL, expires_at>now()  → the link works
      consumed -> consumed_at=<ts>, consumed_by=<uid> → the link is dead
      expired  -> consumed_at=NULL, expires_at<now()  → the link is dead
                  (no UPDATE; expiry is computed at lookup time)
      revoked  -> Admin DELETEs the row OR sets expires_at=now()

    The composite index `ix_invite_pending` powers the admin's
    "show me pending invitations" view in O(log n).
    """

    __tablename__ = "invite_tokens"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # SHA-256 hex = 64 chars exactly. Unique because collisions on a
    # 256-bit random source are computationally impossible — but the
    # constraint is here for DB-level integrity, not probability.
    token_hash = Column(String(64), unique=True, nullable=False, index=True)

    # The email the invite is FOR. The User row created on consumption
    # inherits this email — the invitee cannot redeem with a different one.
    email = Column(String(255), nullable=False, index=True)

    # Role the User row will be created with on consumption. Cannot be
    # changed post-issue; if the admin wants to invite at a different role,
    # they revoke this invite and issue a new one.
    intended_role = Column(_USER_ROLE_ENUM, nullable=False)

    # WHO issued — REQUIRED. The bootstrap_admin is the root of the trust
    # tree; every subsequent invite must trace back to a real admin row.
    created_by_user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    created_by = relationship(
        "User",
        back_populates="invites_issued",
        foreign_keys=[created_by_user_id],
    )

    created_at = Column(DateTime(timezone=True), default=get_ph_now, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)

    # NULL until consumed; once set, the token is permanently dead.
    consumed_at = Column(DateTime(timezone=True), nullable=True)
    consumed_by_user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )

    __table_args__ = (
        # Speeds up "list pending invitations" — a covering index would be
        # overkill at MSME scale; the email+consumed_at composite is enough.
        Index("ix_invite_pending", "email", "consumed_at"),
    )

    def __repr__(self) -> str:
        state = "consumed" if self.consumed_at else "pending"
        return f"<InviteToken id={self.id} email={self.email!r} role={self.intended_role.value} {state}>"


# ═════════════════════════════════════════════════════════════════════════════
# 3. REFRESH TOKEN — hashed-at-rest, revocable, one-per-device.
# ═════════════════════════════════════════════════════════════════════════════

class RefreshToken(Base):
    """
    A long-lived session credential. Stored as SHA-256 hex of the JWT string,
    NOT the JWT itself.

    Why store refresh tokens at all (JWT statelessness should be enough)?
      - Revocation. Without a server-side row, "log out" is a lie — the
        token keeps working until expiry. With a row, logout = DELETE row.
      - Per-device sessions. Future feature: "show me all my logged-in
        devices, sign out the one I lost."
      - Reuse detection. Industry-standard refresh rotation: when a refresh
        token is used, we ROTATE it (mint a new one, mark the old consumed).
        If the OLD token is later presented again, that's a replay attack —
        we revoke ALL tokens for the user. Phase 2 service implements.

    Why hash and not store plaintext?
      - DB dump → token theft. Same threat model as passwords/invites.
      - SHA-256 (not Argon2) is correct here for the same reason as invite
        tokens: the input is already 256 bits of entropy from the JWT
        signing process; a slow KDF buys nothing.
    """

    __tablename__ = "refresh_tokens"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    user = relationship("User", back_populates="refresh_tokens")

    # SHA-256 hex of the JWT string. Indexed for the "presented token"
    # lookup at /auth/refresh.
    token_hash = Column(String(64), unique=True, nullable=False, index=True)

    issued_at = Column(DateTime(timezone=True), default=get_ph_now, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)

    # NULL = the token is still valid. Set when:
    #   - the user logs out (we DELETE the row, not just set this)
    #   - the token is rotated (consumed_at = now, replaced_by_id = new.id)
    #   - the user is deactivated (we DELETE all their tokens)
    consumed_at = Column(DateTime(timezone=True), nullable=True)

    # Forms the rotation chain. NULL on the latest live token; populated
    # when this token has been rotated forward. Reuse-detection compares
    # an incoming token's row state: consumed + replaced_by_id != NULL
    # means "this is an old token someone is replaying."
    replaced_by_id = Column(
        UUID(as_uuid=True), ForeignKey("refresh_tokens.id"), nullable=True
    )

    # Best-effort device fingerprint, populated from the User-Agent header.
    # Useful for the future "active sessions" view; not used for any
    # security decision (UA is trivially spoofable).
    user_agent = Column(String(500), nullable=True)

    def __repr__(self) -> str:
        state = "consumed" if self.consumed_at else "active"
        return f"<RefreshToken id={self.id} user_id={self.user_id} {state}>"


# ═════════════════════════════════════════════════════════════════════════════
# 3.5 PASSWORD RESET TOKEN — hashed-at-rest, time-bound, single-use.
# ═════════════════════════════════════════════════════════════════════════════

class PasswordResetToken(Base):
    """
    A pending or consumed password-reset request.
    
    Lifecycle mirrors InviteToken: created → consumed | expired.
    The `initiated_by_user_id` column distinguishes the two flows:
      NULL          → self-service (user clicked "Forgot Password")
      <admin_uuid>  → admin-initiated reset
    
    On consumption (POST /auth/reset-password):
      1. Verify token hash matches an unconsumed, unexpired row
      2. Update user.hashed_password
      3. Set consumed_at = now()
      4. Revoke ALL refresh tokens for the user (forces clean re-login)
      5. Write PASSWORD_RESET_COMPLETED audit row
    
    All four operations happen in a single transaction. Any failure
    rolls back the entire chain — preserves consistency between
    password state, token state, and session state.
    """

    __tablename__ = "password_resets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    token_hash = Column(String(64), unique=True, nullable=False, index=True)
    initiated_by_user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    created_at = Column(DateTime(timezone=True), default=get_ph_now, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    consumed_at = Column(DateTime(timezone=True), nullable=True)
    ip_address_initiated = Column(String(45), nullable=True)
    ip_address_consumed = Column(String(45), nullable=True)

    user = relationship("User", foreign_keys=[user_id])
    initiated_by = relationship("User", foreign_keys=[initiated_by_user_id])

    def __repr__(self) -> str:
        state = "consumed" if self.consumed_at else "pending"
        flow = "self" if self.initiated_by_user_id is None else "admin"
        return f"<PasswordResetToken id={self.id} user_id={self.user_id} {flow} {state}>"

# ═════════════════════════════════════════════════════════════════════════════
# 4. AUDIT LOG — append-only ledger.
# ═════════════════════════════════════════════════════════════════════════════

class AuditLog(Base):
    """
    The immutable forensic record. The application MUST NOT issue UPDATE or
    DELETE against this table. Append-only is enforced by:
      1. Code review discipline (no UPDATE/DELETE in audit_service).
      2. Phase 6 may add a BEFORE UPDATE/DELETE Postgres trigger that
         RAISEs. We do not add it in v1 because Alembic autogenerate doesn't
         capture triggers cleanly; we'll handle it in a dedicated migration.

    Why BigInteger (not UUID) for the PK:
      - This table sees the highest write volume of any in the system
        (every login, every retrain, every CRUD action). A monotonically
        increasing integer is friendlier to the btree on the PK.
      - The audit log is internal — nobody constructs URLs against
        /admin/audit-logs/<id>; the index is a query convenience, not an
        externally referenced identifier. The enumeration-resistance argument
        for User UUIDs does not apply.

    Why `user_email_snapshot` is denormalized:
      - This is the SINGLE most important traceability decision in the
        schema. If `user_id` were the only link and a user is later
        deactivated/deleted/anonymized, the audit log becomes a list of
        orphan UUIDs. The snapshot column captures the email AT THE TIME
        OF ACTION — exactly what an auditor wants. The cost is negligible
        storage and theoretical staleness, both fine.

    Scaling note (flagged for thesis defense):
      At MSME volume (a few thousand actions/month) this table is fine for
      years. At enterprise volume (millions of rows) we'd partition monthly
      by `timestamp` via pg_partman so cold months can be detached. The
      `ix_audit_timestamp_desc` index already supports a future migration to
      partitioning without schema changes. We do not partition in v1 —
      premature optimization at KJS scale.
    """

    __tablename__ = "audit_logs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)

    timestamp = Column(
        DateTime(timezone=True), default=get_ph_now, nullable=False, index=True
    )

    # NULLABLE: failed-login events have no resolved user.
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    # See class docstring. NEVER stop populating this column.
    user_email_snapshot = Column(String(255), nullable=True)

    # Free-form but disciplined. Phase 3 introduces a `Literal` type alias
    # in services/audit_service.py that pins the vocabulary at type-check
    # time. The DB column itself is String for forward compatibility.
    # Examples: LOGIN, LOGIN_FAILED, USER_CREATED, USER_DEACTIVATED,
    # INVITE_ISSUED, INVITE_CONSUMED, FORECAST_RUN, MODEL_DELETED,
    # MODEL_RENAMED, DATA_UPLOADED, SETTINGS_UPDATED, EXPORT_GENERATED.
    action_type = Column(String(64), nullable=False, index=True)

    # Coarse-grained module bucket. Examples: auth, user_management,
    # forecast, ingestion, settings, export.
    module = Column(String(32), nullable=False, index=True)

    # Free-form identifier of WHAT was acted upon. e.g. "model_id=42",
    # "user_id=3f29...", "settings_key=forecast_threshold_pct". Plain text
    # by design — the structured detail goes in extra_metadata.
    target_resource = Column(String(120), nullable=True)

    status = Column(_AUDIT_STATUS_ENUM, nullable=False)

    # PostgreSQL INET handles both IPv4 and IPv6, with built-in validation.
    # On SQLite (local dev), this falls back to TEXT — see migration notes.
    ip_address = Column(INET, nullable=True)

    user_agent = Column(String(500), nullable=True)

    # Flexible context. Examples:
    #   {"old_role": "VIEWER", "new_role": "ANALYST"}
    #   {"failure_reason": "invalid_credentials"}
    #   {"records_ingested": 412, "batch_id": "abc-123"}
    extra_metadata = Column(JSON, nullable=True)

    user = relationship("User", foreign_keys=[user_id])

    __table_args__ = (
        # Powers the Recent Activity Feed AND the date-filtered admin view.
        # Sorting DESC matches the dominant query pattern; scanning the
        # newest 20 rows is O(log n) → 20 leaf reads.
        Index("ix_audit_timestamp_desc", timestamp.desc()),
        # Powers the action-type + status filter combo on /admin/audit-logs.
        Index("ix_audit_action_status", "action_type", "status"),
    )

    def __repr__(self) -> str:
        return (
            f"<AuditLog id={self.id} ts={self.timestamp} "
            f"action={self.action_type} status={self.status.value}>"
        )


# ═════════════════════════════════════════════════════════════════════════════
# 5. GLOBAL SETTING — key-value config store.
# ═════════════════════════════════════════════════════════════════════════════

class GlobalSetting(Base):
    """
    Tunable platform-level settings. Key-value over a singleton row because:
      - Adding a new setting is one INSERT, not an Alembic migration.
      - Per-key updated_at and updated_by_user_id fall out for free.
      - The value can be any JSON shape (number, string, list, object)
        without column proliferation.

    The cost is loose typing at the DB layer. We recover it at the API
    boundary in Phase 4 with one Pydantic validator per known key — unknown
    keys return 404, never silently created.
    """

    __tablename__ = "global_settings"

    key = Column(String(64), primary_key=True)

    # Always-an-object envelope: {"value": <whatever>}. We could store
    # bare scalars but JSON columns prefer object roots and this is
    # forward-compatible (room for {"value": ..., "unit": "%"} later).
    value_json = Column(JSON, nullable=False)

    description = Column(String(255), nullable=True)

    updated_at = Column(
        DateTime(timezone=True), default=get_ph_now, onupdate=get_ph_now, nullable=False
    )
    updated_by_user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    updated_by = relationship("User")

    def __repr__(self) -> str:
        return f"<GlobalSetting key={self.key!r}>"


# ═════════════════════════════════════════════════════════════════════════════
# Public re-exports — anything importing `domain.auth_models` should be
# able to grab the symbols it needs without poking at private names.
# ═════════════════════════════════════════════════════════════════════════════

__all__ = [
    "UserRole",
    "AuditStatus",
    "User",
    "InviteToken",
    "RefreshToken",
    "AuditLog",
    "GlobalSetting",
    "PasswordResetToken",
]