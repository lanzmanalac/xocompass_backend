# services/auth_service.py
"""
Authentication & invite-consumption business logic.

PHASE 3 CHANGES:
  - All audit-row authoring now goes through services.audit_service.log_action,
    which type-checks action_type against the vocabulary in
    services.audit_vocab. Behaviour is unchanged; the wire-level shape of
    audit_logs rows is unchanged. The CALL SITES are now type-safe.

For the original docstring & rationale, see Phase 2's commit history.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from core.security import (
    INVITE_TOKEN_EXPIRE_HOURS,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_invite_token,
    hash_password,
    password_needs_rehash,
    verify_password,
    TOKEN_TYPE_REFRESH,
)
from domain.auth_models import (
    AuditStatus,
    InviteToken,
    RefreshToken,
    User,
    UserRole,
)
from repository import auth_repository as repo
from services import audit_service  # ── PHASE 3 ──

logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# Service-layer exceptions (unchanged from Phase 2).
# ═════════════════════════════════════════════════════════════════════════════


class AuthError(Exception):
    """Base class for all auth-service errors."""


class InvalidCredentialsError(AuthError):
    pass


class InvalidTokenError(AuthError):
    pass


class InvalidInviteError(AuthError):
    pass


class EmailAlreadyRegisteredError(AuthError):
    pass


class TokenReplayError(AuthError):
    pass


# ═════════════════════════════════════════════════════════════════════════════
# Result containers (unchanged from Phase 2).
# ═════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    access_expires_at: datetime
    refresh_expires_at: datetime


@dataclass(frozen=True)
class LoginResult:
    user: User
    tokens: TokenPair


# ═════════════════════════════════════════════════════════════════════════════
# 1. LOGIN
# ═════════════════════════════════════════════════════════════════════════════


def authenticate_user(
    db: Session,
    *,
    email: str,
    password: str,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> LoginResult:
    email_normalized = email.strip().lower()
    user = repo.fetch_user_by_email(db, email_normalized)

    if user is None:
        verify_password(password, _DUMMY_ARGON2_HASH)
        _record_failed_login(db, email_normalized, ip_address, user_agent,
                             reason="unknown_email")
        db.commit()
        raise InvalidCredentialsError("Invalid email or password.")

    if not user.is_active:
        verify_password(password, _DUMMY_ARGON2_HASH)
        _record_failed_login(db, email_normalized, ip_address, user_agent,
                             reason="inactive_account", user_id=user.id)
        db.commit()
        raise InvalidCredentialsError("Invalid email or password.")

    if not user.hashed_password or not verify_password(password, user.hashed_password):
        _record_failed_login(db, email_normalized, ip_address, user_agent,
                             reason="invalid_password", user_id=user.id)
        db.commit()
        raise InvalidCredentialsError("Invalid email or password.")

    # ── Success path ────────────────────────────────────────────────────────
    if password_needs_rehash(user.hashed_password):
        user.hashed_password = hash_password(password)

    repo.update_user_last_login(db, user)
    tokens = _mint_token_pair(db, user, user_agent=user_agent)

    audit_service.log_action(
        db,
        action_type="LOGIN",
        status=AuditStatus.SUCCESS,
        actor=user,
        target_resource={"user_id": user.id},
        ip_address=ip_address,
        user_agent=user_agent,
        metadata={"role": user.role.value},
    )

    db.commit()
    return LoginResult(user=user, tokens=tokens)


from core.security import hash_password as _seed_hash
_DUMMY_ARGON2_HASH = _seed_hash("xocompass_timing_equalizer_v1")


def _record_failed_login(
    db: Session,
    email: str,
    ip_address: Optional[str],
    user_agent: Optional[str],
    *,
    reason: str,
    user_id: Optional[UUID] = None,
) -> None:
    """Audit a failed login. The reason is captured in metadata for forensics;
    the wire response is always opaque."""
    # We do not have a User object here — pass actor_email_override for
    # the snapshot column, and user_id is None unless the email matched
    # a real user (wrong-password / inactive cases).
    audit_service.log_action(
        db,
        action_type="LOGIN_FAILED",
        status=AuditStatus.FAILED,
        actor=None,
        actor_email_override=email,
        ip_address=ip_address,
        user_agent=user_agent,
        metadata={"reason": reason, **({"user_id": str(user_id)} if user_id else {})},
    )


# ═════════════════════════════════════════════════════════════════════════════
# 2. TOKEN REFRESH (with rotation + reuse detection)
# ═════════════════════════════════════════════════════════════════════════════


def refresh_session(
    db: Session,
    *,
    refresh_token: str,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> TokenPair:
    try:
        payload = decode_token(refresh_token, expected_type=TOKEN_TYPE_REFRESH)
    except Exception as exc:
        logger.info("refresh_session: token decode failed (%s)", type(exc).__name__)
        raise InvalidTokenError("Invalid or expired refresh token.") from exc

    user_id_raw = payload.get("sub")
    user = repo.fetch_user_by_id(db, user_id_raw) if user_id_raw else None
    if user is None or not user.is_active:
        raise InvalidTokenError("Invalid or expired refresh token.")

    token_hash = _sha256_hex(refresh_token)
    row = repo.fetch_refresh_token_by_hash(db, token_hash)

    if row is None or row.user_id != user.id:
        raise InvalidTokenError("Invalid or expired refresh token.")

    if row.consumed_at is not None:
        # REPLAY.
        revoked = repo.revoke_all_refresh_tokens_for_user(db, user.id)
        audit_service.log_action(
            db,
            action_type="TOKEN_REPLAY_DETECTED",
            status=AuditStatus.FAILED,
            actor=user,
            target_resource={"refresh_token_id": row.id},
            ip_address=ip_address,
            user_agent=user_agent,
            metadata={"revoked_count": revoked},
        )
        db.commit()
        logger.warning(
            "Refresh-token replay for user %s; revoked %d tokens.",
            user.id, revoked,
        )
        raise TokenReplayError("Refresh token replay detected.")

    if row.expires_at < datetime.now(timezone.utc):
        raise InvalidTokenError("Invalid or expired refresh token.")

    # ── Rotation ───────────────────────────────────────────────────────────
    new_tokens = _mint_token_pair(db, user, user_agent=user_agent)
    new_row = repo.fetch_refresh_token_by_hash(db, _sha256_hex(new_tokens.refresh_token))
    repo.mark_refresh_token_consumed(db, token_row=row, replaced_by=new_row)

    audit_service.log_action(
        db,
        action_type="TOKEN_REFRESHED",
        status=AuditStatus.SUCCESS,
        actor=user,
        target_resource={"refresh_token_id": row.id},
        ip_address=ip_address,
        user_agent=user_agent,
    )

    db.commit()
    return new_tokens


# ═════════════════════════════════════════════════════════════════════════════
# 3. LOGOUT
# ═════════════════════════════════════════════════════════════════════════════


def logout(
    db: Session,
    *,
    refresh_token: str,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> None:
    try:
        payload = decode_token(refresh_token, expected_type=TOKEN_TYPE_REFRESH)
    except Exception:
        return  # silent succeed

    user_id_raw = payload.get("sub")
    user = repo.fetch_user_by_id(db, user_id_raw) if user_id_raw else None

    token_hash = _sha256_hex(refresh_token)
    row = repo.fetch_refresh_token_by_hash(db, token_hash)
    if row is not None and row.consumed_at is None:
        repo.mark_refresh_token_consumed(db, token_row=row, replaced_by=None)

    if user is not None:
        audit_service.log_action(
            db,
            action_type="LOGOUT",
            status=AuditStatus.SUCCESS,
            actor=user,
            target_resource={"user_id": user.id},
            ip_address=ip_address,
            user_agent=user_agent,
        )

    db.commit()


# ═════════════════════════════════════════════════════════════════════════════
# 4. INVITE CONSUMPTION (atomic)
# ═════════════════════════════════════════════════════════════════════════════


def consume_invite(
    db: Session,
    *,
    raw_token: str,
    full_name: str,
    password: str,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> LoginResult:
    incoming_hash = hash_invite_token(raw_token)

    invite = repo.fetch_invite_by_hash_for_update(db, incoming_hash)
    now = datetime.now(timezone.utc)

    if invite is None:
        raise InvalidInviteError("Invalid or expired invitation.")
    if invite.consumed_at is not None:
        raise InvalidInviteError("Invalid or expired invitation.")
    if invite.expires_at < now:
        raise InvalidInviteError("Invalid or expired invitation.")

    existing = repo.fetch_user_by_email(db, invite.email)
    if existing is not None:
        raise EmailAlreadyRegisteredError(
            "An account already exists for this email."
        )

    user = User(
        email=invite.email,
        full_name=full_name.strip(),
        hashed_password=hash_password(password),
        role=invite.intended_role,
        is_active=True,
        created_by_user_id=invite.created_by_user_id,
    )
    db.add(user)
    db.flush()

    invite.consumed_at = now
    invite.consumed_by_user_id = user.id

    audit_service.log_action(
        db,
        action_type="INVITE_CONSUMED",
        status=AuditStatus.SUCCESS,
        actor=user,
        target_resource={"invite_id": invite.id},
        ip_address=ip_address,
        user_agent=user_agent,
        metadata={"role": user.role.value},
    )
    audit_service.log_action(
        db,
        action_type="USER_CREATED",
        status=AuditStatus.SUCCESS,
        actor=user,
        target_resource={"user_id": user.id},
        ip_address=ip_address,
        user_agent=user_agent,
        metadata={"method": "invite", "role": user.role.value},
    )

    tokens = _mint_token_pair(db, user, user_agent=user_agent)

    audit_service.log_action(
        db,
        action_type="LOGIN",
        status=AuditStatus.SUCCESS,
        actor=user,
        target_resource={"user_id": user.id},
        ip_address=ip_address,
        user_agent=user_agent,
        metadata={"role": user.role.value, "via": "invite_register"},
    )

    db.commit()
    return LoginResult(user=user, tokens=tokens)


# ═════════════════════════════════════════════════════════════════════════════
# Helpers (unchanged from Phase 2)
# ═════════════════════════════════════════════════════════════════════════════


def _mint_token_pair(
    db: Session,
    user: User,
    *,
    user_agent: Optional[str],
) -> TokenPair:
    access_token, access_expires_at = create_access_token(
        subject=str(user.id), role=user.role.value
    )
    refresh_token, refresh_expires_at = create_refresh_token(subject=str(user.id))
    repo.insert_refresh_token(
        db,
        user_id=user.id,
        token_hash=_sha256_hex(refresh_token),
        expires_at=refresh_expires_at,
        user_agent=user_agent,
    )
    return TokenPair(
        access_token=access_token,
        refresh_token=refresh_token,
        access_expires_at=access_expires_at,
        refresh_expires_at=refresh_expires_at,
    )


def _sha256_hex(s: str) -> str:
    import hashlib
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

# ═════════════════════════════════════════════════════════════════════════════
# PHASE 4 ADMIN SERVICES
# ═════════════════════════════════════════════════════════════════════════════


class LastAdminError(AuthError):
    """Refuse to demote/deactivate the last active Admin — would lock the system out."""


class DuplicateInviteError(AuthError):
    """An active invite already exists for this email."""


class EmailAlreadyExistsError(AuthError):
    """A User row already exists for this email."""


# ── Users ───────────────────────────────────────────────────────────────────


def admin_update_user(
    db: Session,
    *,
    actor: User,
    target_user: User,
    full_name: Optional[str],
    role: Optional[UserRole],
    request=None,
) -> User:
    """
    Admin patches a user's full_name and/or role.

    GUARDS:
      - Cannot demote the last active Admin (would lock out the system).
      - Cannot change one's own role (admin self-demotion footgun).
        An admin who wants to demote themselves must have a peer admin
        do it.

    Audits USER_RENAMED and/or USER_ROLE_CHANGED on success.
    """
    old_name = target_user.full_name
    old_role = target_user.role

    will_change_role = role is not None and role != old_role
    will_change_name = full_name is not None and full_name.strip() != old_name

    if will_change_role:
        if target_user.id == actor.id:
            raise LastAdminError("Admins cannot change their own role.")
        if old_role == UserRole.ADMIN and role != UserRole.ADMIN:
            if repo.count_active_admins(db) <= 1:
                raise LastAdminError(
                    "Cannot demote the last active Admin."
                )

    repo.update_user_fields(db, target_user, full_name=full_name, role=role)

    if will_change_name:
        audit_service.log_action(
            db,
            action_type="USER_RENAMED",
            status=AuditStatus.SUCCESS,
            actor=actor,
            target_resource={"user_id": target_user.id},
            request=request,
            metadata={"old_name": old_name, "new_name": target_user.full_name},
        )
    if will_change_role:
        audit_service.log_action(
            db,
            action_type="USER_ROLE_CHANGED",
            status=AuditStatus.SUCCESS,
            actor=actor,
            target_resource={"user_id": target_user.id},
            request=request,
            metadata={
                "old_role": old_role.value,
                "new_role": target_user.role.value,
            },
        )

    db.commit()
    return target_user


def admin_set_user_active(
    db: Session,
    *,
    actor: User,
    target_user: User,
    active: bool,
    request=None,
) -> User:
    """
    Admin activates or deactivates a user.

    GUARDS:
      - Cannot deactivate self (lockout footgun).
      - Cannot deactivate the last active Admin.

    On deactivation, ALL the target's refresh tokens are revoked — they
    can no longer obtain a new access token even if they hold a valid
    refresh token.
    """
    if not active:
        if target_user.id == actor.id:
            raise LastAdminError("Admins cannot deactivate themselves.")
        if target_user.role == UserRole.ADMIN and target_user.is_active:
            if repo.count_active_admins(db) <= 1:
                raise LastAdminError(
                    "Cannot deactivate the last active Admin."
                )

    if target_user.is_active == active:
        # No-op — still audit, marked noop, so the "all admin actions"
        # ledger is complete.
        audit_service.log_action(
            db,
            action_type="USER_REACTIVATED" if active else "USER_DEACTIVATED",
            status=AuditStatus.SUCCESS,
            actor=actor,
            target_resource={"user_id": target_user.id},
            request=request,
            metadata={"noop": True},
        )
        db.commit()
        return target_user

    repo.set_user_active(db, target_user, active)

    revoked_count = 0
    if not active:
        revoked_count = repo.revoke_all_refresh_tokens_for_user(db, target_user.id)

    audit_service.log_action(
        db,
        action_type="USER_REACTIVATED" if active else "USER_DEACTIVATED",
        status=AuditStatus.SUCCESS,
        actor=actor,
        target_resource={"user_id": target_user.id},
        request=request,
        metadata={"revoked_refresh_tokens": revoked_count} if not active else {},
    )

    db.commit()
    return target_user


# ── Invitations ─────────────────────────────────────────────────────────────


def admin_issue_invite(
    db: Session,
    *,
    actor: User,
    email: str,
    intended_role: UserRole,
    request=None,
) -> tuple[InviteToken, str]:
    """
    Issue a new invite. Returns (invite_row, raw_token). The plaintext is
    returned EXACTLY ONCE; the caller (the router) embeds it in the
    response and never persists it.

    GUARDS:
      - The email must not already correspond to an existing User row.
      - The email must not already have an active (unconsumed, unexpired)
        invite. An admin who wants to re-invite must revoke the old one
        first.
    """
    from core.security import generate_invite_token

    email_norm = email.strip().lower()

    if repo.fetch_user_by_email(db, email_norm) is not None:
        raise EmailAlreadyExistsError(
            "A user with this email already exists."
        )

    if repo.fetch_pending_invite_for_email(db, email_norm) is not None:
        raise DuplicateInviteError(
            "An active invitation already exists for this email."
        )

    raw, token_hash = generate_invite_token()
    expires_at = datetime.now(timezone.utc) + timedelta(
        hours=INVITE_TOKEN_EXPIRE_HOURS
    )
    invite = repo.insert_invite_token(
        db,
        token_hash=token_hash,
        email=email_norm,
        intended_role=intended_role,
        created_by_user_id=actor.id,
        expires_at=expires_at,
    )

    audit_service.log_action(
        db,
        action_type="INVITE_ISSUED",
        status=AuditStatus.SUCCESS,
        actor=actor,
        target_resource={"invite_id": invite.id, "email": email_norm},
        request=request,
        metadata={"intended_role": intended_role.value},
    )

    db.commit()
    return invite, raw


def admin_revoke_invite(
    db: Session,
    *,
    actor: User,
    invite: InviteToken,
    request=None,
) -> InviteToken:
    """
    Revoke a pending invite by setting expires_at = now(). Idempotent
    against an already-expired or already-consumed invite — but only
    consumed invites do not get a fresh audit row (no state change).
    """
    if invite.consumed_at is not None:
        # Don't audit — nothing changed and consumed invites can't be
        # un-consumed.
        return invite

    repo.revoke_invite(db, invite)

    audit_service.log_action(
        db,
        action_type="INVITE_REVOKED",
        status=AuditStatus.SUCCESS,
        actor=actor,
        target_resource={"invite_id": invite.id, "email": invite.email},
        request=request,
        metadata={"intended_role": invite.intended_role.value},
    )

    db.commit()
    return invite