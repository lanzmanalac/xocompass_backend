# scripts/smoke_test_auth.py
"""
End-to-end smoke test for the Phase 2 auth surface.

Exercises every endpoint in /auth/* against a running FastAPI app:
  1. POST /auth/login         (with the bootstrap admin)
  2. GET  /auth/me            (verify access token works)
  3. POST /auth/refresh       (rotate the refresh token)
  4. GET  /auth/me            (verify the new access token works)
  5. POST /auth/refresh       (replay the OLD refresh token → must 401)
  6. POST /auth/login         (must STILL work — the user themselves
                              isn't locked, only their tokens were burned)
  7. Cheat-create an invite directly via the service (Phase 4 will
     replace this with POST /admin/invitations).
  8. POST /auth/register      (consume the invite, get a fresh user)
  9. POST /auth/login         (as the new user — confirm credentials work)
 10. POST /auth/logout        (revoke the new user's refresh token)

Run:
    # Terminal 1: start the API
    uvicorn api.main:app --reload

    # Terminal 2: run the smoke test
    python -m scripts.smoke_test_auth

    # OR pass a custom base URL:
    BASE_URL=http://127.0.0.1:8080 python -m scripts.smoke_test_auth

Exit codes:
  0  All steps passed.
  1  A step failed — see stdout for which.
"""

from __future__ import annotations

import os
import sys
from typing import Any

import requests  # part of your existing requirements
import uuid


BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8000").rstrip("/")
ADMIN_EMAIL = os.getenv("SMOKE_ADMIN_EMAIL", "leapziggy@gmail.com")
ADMIN_PASSWORD = os.getenv("SMOKE_ADMIN_PASSWORD")  # required

NEW_USER_EMAIL = os.getenv(
    "SMOKE_NEW_USER_EMAIL",
    f"smoke-analyst-{uuid.uuid4().hex[:8]}@example.com",
)
NEW_USER_PASSWORD = "SmokeTest_LongEnough_2026!"
NEW_USER_NAME = "Smoke Test Analyst"


# ─────────────────────────────────────────────────────────────────────────────
# Tiny assertion helpers — no pytest dependency.
# ─────────────────────────────────────────────────────────────────────────────

PASSED = 0
FAILED = 0


def step(label: str, fn) -> Any:
    global PASSED, FAILED
    try:
        result = fn()
        print(f"  [OK]   {label}")
        PASSED += 1
        return result
    except AssertionError as exc:
        print(f"  [FAIL] {label}: {exc}")
        FAILED += 1
        return None
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAIL] {label}: unexpected {type(exc).__name__}: {exc}")
        FAILED += 1
        return None


def post(path: str, json: dict, headers: dict | None = None) -> requests.Response:
    return requests.post(f"{BASE_URL}{path}", json=json, headers=headers, timeout=15)


def get(path: str, headers: dict | None = None) -> requests.Response:
    return requests.get(f"{BASE_URL}{path}", headers=headers, timeout=15)


# ─────────────────────────────────────────────────────────────────────────────
# Test plan
# ─────────────────────────────────────────────────────────────────────────────


def main() -> int:
    if not ADMIN_PASSWORD:
        print("[ERROR] Set SMOKE_ADMIN_PASSWORD to your bootstrap admin password.")
        return 1

    print(f"\n=== XoCompass Auth Smoke Test ===")
    print(f"BASE_URL = {BASE_URL}")
    print(f"ADMIN    = {ADMIN_EMAIL}\n")

    # ── 1. Bad login: wrong password should 401 with no enumeration ────────
    def bad_login_wrong_password():
        r = post("/auth/login", {"email": ADMIN_EMAIL, "password": "definitely-wrong"})
        assert r.status_code == 401, f"expected 401, got {r.status_code} {r.text}"
        body = r.json()
        assert body["error"]["code"] in ("request_failed", "bad_request",
                                          "internal_server_error", "not_found")
    step("bad login (wrong password) → 401", bad_login_wrong_password)

    def bad_login_unknown_email():
        r = post("/auth/login",
                {"email": "nobody-here@example.com", "password": "whatever"})        
        assert r.status_code == 401, f"expected 401, got {r.status_code} {r.text}"
    step("bad login (unknown email) → 401", bad_login_unknown_email)

    # ── 2. Good login ──────────────────────────────────────────────────────
    login_payload: dict = {}

    def good_login():
        r = post("/auth/login", {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        assert r.status_code == 200, f"expected 200, got {r.status_code} {r.text}"
        body = r.json()
        assert "access_token" in body
        assert "refresh_token" in body
        assert body["user"]["email"].lower() == ADMIN_EMAIL.lower()
        assert body["user"]["role"] == "ADMIN"
        login_payload.update(body)
    step("good login → 200 + tokens + user", good_login)
    if FAILED:
        print("\nAborting: cannot proceed without a successful login.\n")
        return 1

    # ── 3. /auth/me with the access token ──────────────────────────────────
    auth_header = {"Authorization": f"Bearer {login_payload['access_token']}"}

    def me_works():
        r = get("/auth/me", headers=auth_header)
        assert r.status_code == 200, f"expected 200, got {r.status_code} {r.text}"
        body = r.json()
        assert body["email"].lower() == ADMIN_EMAIL.lower()
        assert body["role"] == "ADMIN"
    step("GET /auth/me with access token → 200", me_works)

    def me_without_token():
        r = get("/auth/me")
        assert r.status_code == 401
    step("GET /auth/me without token → 401", me_without_token)

    def me_with_garbage_token():
        r = get("/auth/me", headers={"Authorization": "Bearer not.a.jwt"})
        assert r.status_code == 401
    step("GET /auth/me with garbage token → 401", me_with_garbage_token)

    # ── 4. Refresh: rotate the token, verify the new one works ─────────────
    rotated: dict = {}

    def refresh_rotates():
        r = post("/auth/refresh",
                 {"refresh_token": login_payload["refresh_token"]})
        assert r.status_code == 200, f"expected 200, got {r.status_code} {r.text}"
        body = r.json()
        assert body["access_token"] != login_payload["access_token"], \
            "access token did not rotate"
        assert body["refresh_token"] != login_payload["refresh_token"], \
            "refresh token did not rotate"
        rotated.update(body)
    step("POST /auth/refresh → 200 + rotated tokens", refresh_rotates)

    def new_access_token_works():
        r = get("/auth/me",
                headers={"Authorization": f"Bearer {rotated['access_token']}"})
        assert r.status_code == 200
    step("GET /auth/me with NEW access token → 200", new_access_token_works)

    # ── 5. THE BIG ONE: replay the OLD refresh token → must 401 ────────────
    def old_refresh_is_dead():
        r = post("/auth/refresh",
                 {"refresh_token": login_payload["refresh_token"]})
        assert r.status_code == 401, \
            f"expected 401 on replay, got {r.status_code} {r.text}"
    step("replay OLD refresh token → 401 (replay detected)", old_refresh_is_dead)

    def login_again_after_replay():
        r = post("/auth/login",
                 {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        assert r.status_code == 200, \
            f"expected 200 on re-login, got {r.status_code}"
        # ── PHASE 6 FIX: capture the FRESH tokens. Subsequent steps that
        # need an admin token (admin invitations, admin actions) must use
        # these, not the stale ones from the original good_login.
        login_payload.update(r.json())
    step("re-login after replay → 200 (user not locked)", login_again_after_replay)

    # ── 6. Cheat-create an invite (Phase 4 will replace this) ──────────────
    invite_token: str = ""

    def real_create_invite():
        """Phase 4: use the real admin endpoint instead of the deleted seam."""
        r = post(
            "/admin/invitations",
            {"email": NEW_USER_EMAIL, "role": "ANALYST"},
            headers={"Authorization": f"Bearer {login_payload['access_token']}"},
        )
        assert r.status_code == 201, f"expected 201, got {r.status_code} {r.text}"

        body = r.json()
        assert "invite_url" in body
        # invite_url = "{FRONTEND_BASE_URL}/register?token={raw}"
        # Extract the token from the URL.
        raw = body["invite_url"].split("token=", 1)[-1]
        assert len(raw) >= 40
        nonlocal_holder["raw"] = raw

    nonlocal_holder = {"raw": ""}
    step("real-create invite via POST /admin/invitations", real_create_invite)
    invite_token = nonlocal_holder["raw"]


    # ── 7. Consume the invite via the public endpoint ──────────────────────
    new_user_login: dict = {}

    def consume_invite_endpoint():
        r = post("/auth/register", {
            "invite_token": invite_token,
            "full_name": NEW_USER_NAME,
            "password": NEW_USER_PASSWORD,
        })
        assert r.status_code == 201, f"expected 201, got {r.status_code} {r.text}"
        body = r.json()
        assert body["user"]["email"].lower() == NEW_USER_EMAIL.lower()
        assert body["user"]["role"] == "ANALYST"
        new_user_login.update(body)
    step("POST /auth/register → 201 + auto-login", consume_invite_endpoint)

    def replay_register_fails():
        r = post("/auth/register", {
            "invite_token": invite_token,
            "full_name": NEW_USER_NAME,
            "password": NEW_USER_PASSWORD,
        })
        assert r.status_code in (400, 409), \
            f"expected 400/409 on second consume, got {r.status_code}"
    step("re-consuming the same invite → 400/409", replay_register_fails)

    # ── 8. New user can log in independently ───────────────────────────────
    def new_user_login_works():
        r = post("/auth/login",
                 {"email": NEW_USER_EMAIL, "password": NEW_USER_PASSWORD})
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        assert r.json()["user"]["role"] == "ANALYST"
    step("new user can log in → 200", new_user_login_works)

    # ── 9. Logout ──────────────────────────────────────────────────────────
    def logout_works():
        r = post("/auth/logout",
                 {"refresh_token": new_user_login["refresh_token"]})
        assert r.status_code == 200
    step("POST /auth/logout → 200", logout_works)

    def logout_is_idempotent():
        r = post("/auth/logout",
                 {"refresh_token": new_user_login["refresh_token"]})
        assert r.status_code == 200, "second logout should still 200"
    step("second logout → 200 (idempotent)", logout_is_idempotent)

    # ── Summary ────────────────────────────────────────────────────────────
    print(f"\n=== {PASSED} passed, {FAILED} failed ===\n")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    sys.exit(main())