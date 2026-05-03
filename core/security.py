# core/security.py
"""
Cryptographic primitives for the XoCompass auth subsystem.

This module is the single source of truth for:
  - Password hashing (Argon2id) — verify, hash, needs_update
  - JWT mint/verify (HS256, configurable)
  - Invite token generation (high-entropy random + SHA-256 fingerprint)

DESIGN INVARIANTS:
  1. NEVER take or return plaintext passwords from any function except
     `hash_password` (input) and `verify_password` (input). Plaintext must
     not appear in logs, exception messages, or return values.
  2. NEVER persist a JWT or invite token in plaintext. Storage layer
     (Phase 1) only ever sees hashes.
  3. ALL time-sensitive values (token expiry, issued-at) use UTC at the
     JWT boundary. Display-time conversion to PH timezone happens in the
     API layer, not here. JWT `exp` and `iat` claims are seconds-since-epoch
     (RFC 7519), which is timezone-agnostic by definition.
  4. Configuration is read from environment variables ONCE at module import
     time. Anything that needs runtime mutation (e.g., feature-flagging a
     stricter Argon2 cost) belongs in a future settings module, not here.

This module imports nothing from api/, domain/, services/, or repository/.
It is a pure leaf dependency.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Final
import uuid

from jose import JWTError, jwt
from passlib.context import CryptContext

logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# CONFIGURATION  — read once at module import.
# ═════════════════════════════════════════════════════════════════════════════

def _require_env(name: str) -> str:
    """
    Hard-fail on boot if a required secret is missing OR is still the
    placeholder value from .env.example. This is deliberately loud:
    a misconfigured Cloud Run revision must not start serving traffic
    with a deterministic or empty JWT secret.
    """
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Required environment variable {name} is not set. "
            f"See .env.example for the required configuration surface."
        )
    if value.startswith("REPLACE_WITH"):
        raise RuntimeError(
            f"Environment variable {name} still contains the .env.example "
            f"placeholder. Generate a real value (e.g. `openssl rand -hex 32`) "
            f"and configure it via Secret Manager before booting."
        )
    return value


JWT_SECRET_KEY: Final[str] = _require_env("JWT_SECRET_KEY")
JWT_ALGORITHM: Final[str] = os.getenv("JWT_ALGORITHM", "HS256").strip() or "HS256"

ACCESS_TOKEN_EXPIRE_MINUTES: Final[int] = int(
    os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "15")
)
REFRESH_TOKEN_EXPIRE_DAYS: Final[int] = int(
    os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7")
)
INVITE_TOKEN_EXPIRE_HOURS: Final[int] = int(
    os.getenv("INVITE_TOKEN_EXPIRE_HOURS", "72")
)


# ═════════════════════════════════════════════════════════════════════════════
# PASSWORD HASHING  — Argon2id via passlib.
# ═════════════════════════════════════════════════════════════════════════════
#
# WHY Argon2id (not bcrypt):
#   - Won the 2015 Password Hashing Competition.
#   - Memory-hard: forces an attacker's GPU/ASIC to spend RAM, not just cycles.
#   - Current OWASP 2024+ recommendation.
#
# WHY passlib (not argon2-cffi directly):
#   - PHC string format includes algorithm + cost params + salt + hash in one
#     column. No separate `salt` field. No accidental param drift.
#   - `deprecated="auto"` lets us bump cost parameters in the future and
#     transparently re-hash on next successful login (`needs_update()`).
#
# COST TUNING:
#   passlib's defaults for argon2 in 1.7.4 (time=2, memory=102400 KiB=100 MiB,
#   parallelism=8) target ~50ms on commodity hardware. On Cloud Run with
#   4 workers x 4 CPUs, this is the right ceiling. If `/auth/login` p99 latency
#   ever exceeds 200ms under load, drop memory_cost to 65536 (64 MiB).
# ═════════════════════════════════════════════════════════════════════════════

_pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")


def hash_password(plain: str) -> str:
    """
    Return an Argon2id PHC-format hash of `plain`.
    The output string fully encodes the algorithm, salt, and cost params.
    Store this string verbatim in `users.hashed_password`.
    """
    if not plain:
        # Defensive guard — empty passwords must be rejected at the schema
        # layer (Pydantic min_length), but defense-in-depth here costs nothing.
        raise ValueError("Cannot hash an empty password.")
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """
    Constant-time verification of `plain` against the stored `hashed`.
    Returns False (never raises) on:
      - Hash format mismatch (wrong algorithm, corrupted PHC string)
      - Empty plain or empty hash
      - Any other cryptographic failure
    Caller MUST treat False as "auth failed" and return a generic 401.
    """
    if not plain or not hashed:
        return False
    try:
        return _pwd_context.verify(plain, hashed)
    except Exception as exc:  # passlib raises various ValueErrors on bad input
        logger.warning("Password verification raised %s; treating as failure.", type(exc).__name__)
        return False


def password_needs_rehash(hashed: str) -> bool:
    """
    True if `hashed` was produced under cost params we now consider obsolete.
    Call after a SUCCESSFUL verify_password() and, if True, hash the plaintext
    again with the current params and UPDATE the stored hash. This is the
    transparent migration path that avoids a forced password-reset event when
    we tighten cost parameters.
    """
    return _pwd_context.needs_update(hashed)


# ═════════════════════════════════════════════════════════════════════════════
# JWT — access & refresh token mint/verify.
# ═════════════════════════════════════════════════════════════════════════════
#
# WHY HS256 (not RS256) for v1:
#   - Single issuer (this backend), single audience (this backend).
#   - HMAC is faster than RSA for both sign and verify (microseconds vs ms).
#   - Symmetric key is one secret, not a keypair to rotate independently.
#   - When (if) we ever federate (e.g., a separate auth microservice), we
#     migrate to RS256 by changing JWT_ALGORITHM and adding a public-key
#     endpoint. The token shape stays identical.
#
# WHAT GOES IN THE PAYLOAD:
#   sub  → user UUID (string). Standard subject claim.
#   role → UserRole enum value. Avoids a DB lookup per request.
#   exp  → seconds-since-epoch UTC. Standard expiry claim.
#   iat  → seconds-since-epoch UTC. Standard issued-at claim.
#   typ  → "access" | "refresh". Lets us reject misuse (refresh token
#          presented to a protected endpoint, or vice versa).
#
# WHAT EXPLICITLY DOES NOT GO IN:
#   email, full_name → mutable. JWT carries identifiers only.
#   permissions list → derived from role at the dependency layer (Phase 2).
# ═════════════════════════════════════════════════════════════════════════════

TOKEN_TYPE_ACCESS: Final[str] = "access"
TOKEN_TYPE_REFRESH: Final[str] = "refresh"


def _utcnow() -> datetime:
    """Single source of 'now' for token timestamps. UTC by definition."""
    return datetime.now(timezone.utc)


def create_access_token(*, subject: str, role: str) -> tuple[str, datetime]:
    """
    Mint a short-lived access token. Returns (token, expires_at_utc).
    The expires_at is returned alongside the token so the caller (the login
    endpoint, Phase 2) can include it in the response body for the frontend
    to schedule its silent refresh.
    """
    expires_at = _utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload: dict[str, Any] = {
        "sub": subject,
        "role": role,
        "typ": TOKEN_TYPE_ACCESS,
        "iat": int(_utcnow().timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": str(uuid.uuid4()) 
    }
    token = jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    return token, expires_at


def create_refresh_token(*, subject: str) -> tuple[str, datetime]:
    """
    Mint a long-lived refresh token. Returns (token, expires_at_utc).
    The token itself is a JWT for consistency with access tokens — but we
    will ALSO store a hashed copy in `refresh_tokens` (Phase 1) so we can
    revoke it on logout/deactivation. JWT statelessness gives us cheap
    verification; the DB row gives us revocation. Both layers, no contradiction.
    """
    expires_at = _utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    payload: dict[str, Any] = {
        "sub": subject,
        "typ": TOKEN_TYPE_REFRESH,
        "iat": int(_utcnow().timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": str(uuid.uuid4()),
    }
    token = jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    return token, expires_at


def decode_token(token: str, *, expected_type: str | None = None) -> dict[str, Any]:
    """
    Verify signature, expiry, and (optionally) token type. Returns the payload
    dict on success. Raises `JWTError` on ANY failure — caller (Phase 2
    dependency) catches and translates to HTTP 401 with a generic message.

    Why expected_type matters:
      Without it, an attacker who steals a refresh token could present it as
      an access token. The `typ` claim closes that confusion.
    """
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except JWTError:
        # Re-raise unchanged. The dependency layer maps to 401.
        raise

    if expected_type is not None and payload.get("typ") != expected_type:
        raise JWTError(f"Token type mismatch: expected {expected_type}.")

    return payload


# ═════════════════════════════════════════════════════════════════════════════
# INVITE TOKENS — high-entropy random + SHA-256 fingerprint.
# ═════════════════════════════════════════════════════════════════════════════
#
# THREAT MODEL:
#   The plaintext invite token travels in a URL the admin pastes to the
#   invitee. If our DB is dumped, raw tokens would be live credentials. We
#   therefore store only a hash. SHA-256 is appropriate (NOT bcrypt/argon2)
#   because:
#     - The token is already 256 bits of cryptographic randomness from
#       secrets.token_urlsafe(32) — brute-forcing it is computationally
#       infeasible regardless of the hash function. A slow KDF buys nothing.
#     - SHA-256 is fast enough to use as an indexed lookup key. We hash the
#       incoming token once and `WHERE token_hash = ?` against an index.
#       A bcrypt-style per-row comparison would be O(n) over invite_tokens.
#
# COMPARISON SAFETY:
#   `verify_invite_token` uses hmac.compare_digest to defeat timing attacks,
#   even though the inputs are public hashes — defense in depth costs nothing.
# ═════════════════════════════════════════════════════════════════════════════

# 32 bytes → ~43 chars after URL-safe base64 encoding. Plenty.
INVITE_TOKEN_BYTES: Final[int] = 32

# ═════════════════════════════════════════════════════════════════════════════
# PASSWORD RESET TOKENS — same threat model as invite tokens.
# ═════════════════════════════════════════════════════════════════════════════
#
# We deliberately reuse the invite-token machinery: 32 bytes of
# secrets.token_urlsafe entropy + SHA-256 fingerprint storage.
# The *only* difference is TTL (30 minutes vs 72 hours) — short enough
# that a stolen token is rarely useful, long enough to survive an email
# round-trip + the user reading their inbox.
# ═════════════════════════════════════════════════════════════════════════════

PASSWORD_RESET_TOKEN_EXPIRE_MINUTES: Final[int] = int(
    os.getenv("PASSWORD_RESET_TOKEN_EXPIRE_MINUTES", "30")
)


def generate_password_reset_token() -> tuple[str, str]:
    """
    Return (raw_token, token_hash). Same shape as generate_invite_token().
    The plaintext exists in:
      - The reset URL we email (or return in dev mode)
      - The HTTP request body of POST /auth/reset-password
    It NEVER touches stable storage.
    """
    raw = secrets.token_urlsafe(INVITE_TOKEN_BYTES)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return raw, digest


def hash_password_reset_token(raw_token: str) -> str:
    """SHA-256 hex digest for the indexed lookup at consumption time."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

def generate_invite_token() -> tuple[str, str]:
    """
    Return (raw_token, token_hash). The raw_token is what we put in the URL
    we hand to the admin (and they hand to the invitee). The token_hash is
    what we INSERT into invite_tokens.

    The plaintext exists in:
      - The HTTP response body of POST /admin/invitations (returned exactly once)
      - The URL the admin pastes
      - The HTTP request body of POST /auth/register (single use, then dead)
    It NEVER touches our stable storage.
    """
    raw = secrets.token_urlsafe(INVITE_TOKEN_BYTES)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return raw, digest


def hash_invite_token(raw_token: str) -> str:
    """
    Recompute the SHA-256 hex digest of an incoming raw token, for the
    indexed `WHERE token_hash = ?` lookup at registration time.
    """
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    """
    Constant-time string equality for security-sensitive comparisons.
    Use this whenever you compare two hex digests or two tokens, even when
    both sides are server-controlled.
    """
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


# ═════════════════════════════════════════════════════════════════════════════
# Module-level integrity check.
# ═════════════════════════════════════════════════════════════════════════════

logger.info(
    "core.security initialized: alg=%s access_ttl=%dmin refresh_ttl=%dd invite_ttl=%dh",
    JWT_ALGORITHM,
    ACCESS_TOKEN_EXPIRE_MINUTES,
    REFRESH_TOKEN_EXPIRE_DAYS,
    INVITE_TOKEN_EXPIRE_HOURS,
)

def build_password_reset_url(raw_token: str) -> str:
    """
    Builds the user-facing password reset URL.
    
    SECURITY: Uses the URL fragment (#) rather than a query parameter (?)
    to deliver the token. Fragments are never transmitted to the server,
    so the token does not appear in:
      - Server access logs (the server only sees GET /reset-password)
      - Referer headers sent to analytics, fonts, or other third-party
        resources loaded by the reset page
      - Proxy/CDN access logs
    
    The token still appears in browser history (a local-device concern
    bounded by the 30-minute TTL and single-use enforcement upstream).
    
    ISO 25010 → Security → Confidentiality:
      Mitigates the URL-leakage class of attacks without expanding the
      threat model. Companion mitigations (TTL, single-use, session
      revocation on consume) remain in force.
    
    ISO 25010 → Maintainability → Modularity:
      Single source of truth for the reset URL shape. If we ever
      change the path or fragment scheme, one edit covers all callers.
    """
    import os
    base = (os.getenv("FRONTEND_BASE_URL") or "").rstrip("/")
    if not base:
        raise RuntimeError(
            "FRONTEND_BASE_URL is not configured. "
            "Password reset URLs cannot be constructed."
        )
    return f"{base}/reset-password#token={raw_token}"