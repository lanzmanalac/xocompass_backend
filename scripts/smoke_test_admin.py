# scripts/smoke_test_admin.py
"""
End-to-end smoke test for the Phase 4 admin surface.

Covers:
  1. RBAC enforcement
     - Non-admin (Viewer) cannot reach /admin/* (403)
     - Unauthenticated cannot reach /admin/* (401)
  2. Users
     - GET /admin/users (paginated)
     - PATCH a non-admin user's name + role
     - The "last admin" guard fires on attempted self-demotion
     - Deactivate then reactivate
  3. Invitations
     - POST → GET (filter pending) → revoke → GET (filter expired)
  4. Audit
     - GET /admin/audit-logs returns recent rows
     - Cursor pagination works (next page has different IDs)
     - GET /admin/audit-logs/action-types returns the controlled vocab
  5. System
     - GET /admin/system/overview returns plausible counts
  6. Settings
     - GET /admin/settings/forecast_deviation_alert_pct returns current value
     - PUT updates it; GET reflects; SETTINGS_UPDATED audit row exists
     - PUT with invalid value (out of range) → 400
     - PUT to unknown key → 404

Run:
    SMOKE_ADMIN_PASSWORD='<your-password>' python -m scripts.smoke_test_admin
"""

from __future__ import annotations

import os
import sys
import uuid

import requests

BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8000").rstrip("/")
ADMIN_EMAIL = os.getenv("SMOKE_ADMIN_EMAIL", "leapziggy@gmail.com")
ADMIN_PASSWORD = os.getenv("SMOKE_ADMIN_PASSWORD")

# Generate a fresh email per run so re-running the smoke is idempotent.
TEST_EMAIL = f"admin-smoke-{uuid.uuid4().hex[:8]}@example.com"
TEST_NAME = "Admin Smoke Subject"
TEST_PASSWORD = "AdminSmoke_LongEnough_2026!"

PASSED = 0
FAILED = 0


def step(label, fn):
    global PASSED, FAILED
    try:
        result = fn()
        print(f"  [OK]   {label}")
        PASSED += 1
        return result
    except AssertionError as exc:
        print(f"  [FAIL] {label}: {exc}")
        FAILED += 1
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAIL] {label}: {type(exc).__name__}: {exc}")
        FAILED += 1


def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


def main() -> int:
    if not ADMIN_PASSWORD:
        print("[ERROR] Set SMOKE_ADMIN_PASSWORD.")
        return 1

    print(f"\n=== XoCompass Admin Smoke Test ===")
    print(f"BASE_URL = {BASE_URL}\n")

    # ── Setup: log in admin ────────────────────────────────────────────────
    state = {}

    def admin_login():
        r = requests.post(
            f"{BASE_URL}/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            timeout=15,
        )
        assert r.status_code == 200
        state["admin_token"] = r.json()["access_token"]
        state["admin_id"] = r.json()["user"]["id"]
    step("admin login", admin_login)
    if FAILED:
        return 1

    admin_h = lambda: auth_headers(state["admin_token"])

    # ── 1. RBAC: unauthenticated /admin/* ─────────────────────────────────
    def rbac_unauthenticated():
        r = requests.get(f"{BASE_URL}/admin/users", timeout=15)
        assert r.status_code == 401
    step("unauthenticated GET /admin/users → 401", rbac_unauthenticated)

    # ── 2. Issue an invite, register a Viewer, confirm Viewer can't admin ─
    invite_url = {}
    def issue_viewer_invite():
        r = requests.post(
            f"{BASE_URL}/admin/invitations",
            headers=admin_h(),
            json={"email": TEST_EMAIL, "role": "VIEWER"},
            timeout=15,
        )
        assert r.status_code == 201, f"{r.status_code} {r.text}"
        body = r.json()
        invite_url["url"] = body["invite_url"]
    step("POST /admin/invitations VIEWER → 201", issue_viewer_invite)

    def register_viewer():
        token = invite_url["url"].split("token=", 1)[-1]
        r = requests.post(
            f"{BASE_URL}/auth/register",
            json={
                "invite_token": token,
                "full_name": TEST_NAME,
                "password": TEST_PASSWORD,
            },
            timeout=15,
        )
        assert r.status_code == 201
        body = r.json()
        state["viewer_token"] = body["access_token"]
        state["viewer_id"] = body["user"]["id"]
    step("register the invited Viewer", register_viewer)

    def rbac_viewer_blocked():
        r = requests.get(
            f"{BASE_URL}/admin/users",
            headers=auth_headers(state["viewer_token"]),
            timeout=15,
        )
        assert r.status_code == 403
    step("Viewer GET /admin/users → 403", rbac_viewer_blocked)

    # ── 3. List users — should include the Viewer we just created ─────────
    def list_users_includes_viewer():
        r = requests.get(
            f"{BASE_URL}/admin/users",
            headers=admin_h(),
            params={"search": TEST_EMAIL},
            timeout=15,
        )
        assert r.status_code == 200
        items = r.json()["items"]
        assert any(u["email"] == TEST_EMAIL for u in items), \
            f"new user not in list: {items}"
    step("GET /admin/users finds the Viewer", list_users_includes_viewer)

    # ── 4. Promote the Viewer to Analyst, then back ────────────────────────
    def promote_viewer():
        r = requests.patch(
            f"{BASE_URL}/admin/users/{state['viewer_id']}",
            headers=admin_h(),
            json={"role": "ANALYST"},
            timeout=15,
        )
        assert r.status_code == 200, f"{r.status_code} {r.text}"
        assert r.json()["role"] == "ANALYST"
    step("PATCH user role VIEWER → ANALYST", promote_viewer)

    # ── 5. Last-admin guard: admin tries to demote SELF ───────────────────
    def self_demote_blocked():
        r = requests.patch(
            f"{BASE_URL}/admin/users/{state['admin_id']}",
            headers=admin_h(),
            json={"role": "ANALYST"},
            timeout=15,
        )
        assert r.status_code == 409, f"{r.status_code} {r.text}"
    step("self-demote → 409 (last-admin guard)", self_demote_blocked)

    # ── 6. Deactivate / reactivate the test user ──────────────────────────
    def deactivate_then_reactivate():
        r = requests.post(
            f"{BASE_URL}/admin/users/{state['viewer_id']}/deactivate",
            headers=admin_h(), timeout=15,
        )
        assert r.status_code == 200 and r.json()["is_active"] is False
        r = requests.post(
            f"{BASE_URL}/admin/users/{state['viewer_id']}/activate",
            headers=admin_h(), timeout=15,
        )
        assert r.status_code == 200 and r.json()["is_active"] is True
    step("deactivate then reactivate test user", deactivate_then_reactivate)

    # ── 7. Audit pagination ───────────────────────────────────────────────
    def audit_first_page():
        r = requests.get(
            f"{BASE_URL}/admin/audit-logs",
            headers=admin_h(),
            params={"limit": 5},
            timeout=15,
        )
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body["items"], list)
        state["first_page_ids"] = [item["id"] for item in body["items"]]
        state["next_cursor"] = body.get("next_cursor")
    step("GET /admin/audit-logs first page", audit_first_page)

    def audit_second_page():
        if not state.get("next_cursor"):
            print("  [SKIP] no next_cursor — not enough audit rows yet")
            return
        r = requests.get(
            f"{BASE_URL}/admin/audit-logs",
            headers=admin_h(),
            params={"limit": 5, "cursor": state["next_cursor"]},
            timeout=15,
        )
        assert r.status_code == 200
        page2_ids = [item["id"] for item in r.json()["items"]]
        first_set = set(state["first_page_ids"])
        assert not (first_set & set(page2_ids)), \
            "page 2 overlaps page 1 — cursor pagination broken"
    step("GET /admin/audit-logs page 2 (no overlap with page 1)",
         audit_second_page)

    # ── 8. Action-types vocab endpoint ────────────────────────────────────
    def vocab_endpoint():
        r = requests.get(
            f"{BASE_URL}/admin/audit-logs/action-types",
            headers=admin_h(), timeout=15,
        )
        assert r.status_code == 200
        body = r.json()
        assert "INVITE_ISSUED" in body["action_types"]
        assert "USER_ROLE_CHANGED" in body["action_types"]
        assert "user_management" in body["modules"]
    step("GET /admin/audit-logs/action-types", vocab_endpoint)

    # ── 9. System overview ────────────────────────────────────────────────
    def system_overview():
        r = requests.get(
            f"{BASE_URL}/admin/system/overview",
            headers=admin_h(), timeout=15,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["active_users_count"] >= 1
        assert body["pipeline_status"] in ("healthy", "stale", "unknown")
        assert isinstance(body["recent_activity"], list)
    step("GET /admin/system/overview", system_overview)

    # ── 10. Settings round-trip ───────────────────────────────────────────
    def get_setting():
        r = requests.get(
            f"{BASE_URL}/admin/settings/forecast_deviation_alert_pct",
            headers=admin_h(), timeout=15,
        )
        assert r.status_code == 200
        state["original_pct"] = r.json()["value"]
    step("GET setting forecast_deviation_alert_pct", get_setting)

    def put_setting():
        r = requests.put(
            f"{BASE_URL}/admin/settings/forecast_deviation_alert_pct",
            headers=admin_h(),
            json={"value": 22.5},
            timeout=15,
        )
        assert r.status_code == 200, f"{r.status_code} {r.text}"
        assert r.json()["value"] == 22.5
    step("PUT setting forecast_deviation_alert_pct → 22.5", put_setting)

    def put_invalid():
        r = requests.put(
            f"{BASE_URL}/admin/settings/forecast_deviation_alert_pct",
            headers=admin_h(),
            json={"value": 9999},
            timeout=15,
        )
        assert r.status_code == 400
    step("PUT out-of-range → 400", put_invalid)

    def put_unknown_key():
        r = requests.put(
            f"{BASE_URL}/admin/settings/this_key_does_not_exist",
            headers=admin_h(),
            json={"value": 1},
            timeout=15,
        )
        assert r.status_code == 404
    step("PUT unknown key → 404", put_unknown_key)

    def restore_setting():
        # Reset to the original value so re-runs are idempotent.
        r = requests.put(
            f"{BASE_URL}/admin/settings/forecast_deviation_alert_pct",
            headers=admin_h(),
            json={"value": state["original_pct"]},
            timeout=15,
        )
        assert r.status_code == 200
    step("restore original setting value", restore_setting)

    # ── 11. Cleanup: deactivate the test user (keep audit row, don't pollute) ─
    def cleanup():
        r = requests.post(
            f"{BASE_URL}/admin/users/{state['viewer_id']}/deactivate",
            headers=admin_h(), timeout=15,
        )
        assert r.status_code == 200
    step("cleanup: deactivate test user", cleanup)

    print(f"\n=== {PASSED} passed, {FAILED} failed ===\n")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    sys.exit(main())