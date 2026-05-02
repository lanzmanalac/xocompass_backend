# core/rate_limit.py
"""
Rate-limiting primitives for the auth surface.

DESIGN INVARIANTS:
  1. The keying function uses the resolved client IP from X-Forwarded-For
     when running behind Cloud Run (which always sets it). Local dev
     falls back to the socket peer.
  2. Limits are per-IP per-window, NOT per-account. Per-account limiting
     would let an attacker who knows the email lock the legitimate user
     out — a denial-of-service vector. Per-IP allows credential stuffing
     to be slowed without weaponizable lockouts.
  3. The Limiter instance is module-level and reused across imports —
     `slowapi` requires this for the in-memory bucket to actually share
     state across endpoint decorators.

ISO 25010 → Security → Resistance to Attack.
ISO 25010 → Reliability → Fault Tolerance under adversarial load.
"""

from __future__ import annotations

from fastapi import Request
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address


def _client_ip_keyfunc(request: Request) -> str:
    """
    Prefer X-Forwarded-For (Cloud Run sets it) over the socket peer, so
    the limit applies to the actual originating client rather than the
    Cloud Run load balancer's hop. Falls back to slowapi's default which
    reads request.client.host directly — useful in local development.
    """
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        # Leftmost entry = original client.
        return fwd.split(",")[0].strip()
    return get_remote_address(request)


# Single module-level limiter. DO NOT instantiate Limiter elsewhere — its
# in-memory store is per-instance and we want one bucket per Cloud Run
# instance, not per import.
limiter = Limiter(key_func=_client_ip_keyfunc, default_limits=[])


__all__ = ["limiter", "RateLimitExceeded"]