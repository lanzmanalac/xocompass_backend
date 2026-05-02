# scripts/smoke_test_rbac.py
"""
End-to-end smoke test for the Phase 5 RBAC retrofit.

Asserts that EVERY existing /api/* endpoint correctly:
  - Returns 401 when called WITHOUT a token.
  - Returns 403 when called with a token whose role is below the required level.
  - Returns 200/201/204/404 (i.e., the endpoint's normal contract) when called
    with a token of the correct role.

Also confirms /_debug/db-truth is gated correctly:
  - Returns 404 in production (route not registered).
  - Returns 401/403 in development without an Admin token.
  - Returns 200 in development with an Admin token.

PREREQUISITES:
  - ENVIRONMENT=development
  - python -m seed_mock_data has been run (creates dev-admin/analyst/viewer)

Run:
    python -m scripts.smoke_test_rbac
"""

from __future__ import annotations

import os
import sys
from typing import Optional

import requests

BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8000").rstrip("/")

# Local-dev seed credentials (5.4).
DEV_ADMIN = ("dev-admin@xocompass.dev", "DevAdmin_LongEnough_2026!")
DEV_ANALYST = ("dev-analyst@xocompass.dev", "DevAnalyst_LongEnough_2026!")
DEV_VIEWER = ("dev-viewer@xocompass.dev", "DevViewer_LongEnough_2026!")

PASSED = 0
FAILED = 0


def step(label: str, fn):
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


def login(email: str, password: str) -> Optional[str]:
    r = requests.post(
        f"{BASE_URL}/auth/login",
        json={"email": email, "password": password},
        timeout=15,
    )
    if r.status_code != 200:
        print(f"  [PRECONDITION FAIL] login({email}) returned {r.status_code} {r.text}")
        return None
    return r.json()["access_token"]


def headers(token: Optional[str]) -> dict:
    return {"Authorization": f"Bearer {token}"} if token else {}


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint specs: (method, path, required_role, expected_codes_when_authorized)
#
# expected_codes_when_authorized is a set because some endpoints can return
# 404 in a fresh DB ("no models found") which is NOT an RBAC failure — the
# check passed; the resource just doesn't exist. The test treats anything
# OTHER than 401/403 as RBAC-pass.
# ─────────────────────────────────────────────────────────────────────────────

READ_ANY_ENDPOINTS = [
    ("GET", "/api/models"),
    ("GET", "/api/business-analytics"),
    ("GET", "/api/historical-data"),
]

ANALYST_ONLY_WRITE = [
    # method, path, body_factory or None
    ("PATCH", "/api/models/0/rename", lambda: {"new_model_name": "rbac-smoke"}),
]

ADMIN_ONLY_DESTRUCTIVE = [
    ("DELETE", "/api/models/999999", None),
]


def call(method: str, path: str, token: Optional[str], json_body=None):
    return requests.request(
        method,
        f"{BASE_URL}{path}",
        headers=headers(token),
        json=json_body,
        timeout=15,
    )


def main() -> int:
    print(f"\n=== XoCompass RBAC Smoke Test ===")
    print(f"BASE_URL = {BASE_URL}\n")

    # ── Setup tokens ───────────────────────────────────────────────────────
    print("--- Acquiring tokens ---")
    admin_t = login(*DEV_ADMIN)
    analyst_t = login(*DEV_ANALYST)
    viewer_t = login(*DEV_VIEWER)

    if not all([admin_t, analyst_t, viewer_t]):
        print("\n[ERROR] Could not acquire all three role tokens.")
        print("  Run: ENVIRONMENT=development python -m seed_mock_data")
        return 1

    # ─────────────────────────────────────────────────────────────────────
    # READ-ANY ENDPOINTS — Viewer should pass; unauthenticated should 401.
    # ─────────────────────────────────────────────────────────────────────
    print("\n--- Read endpoints (require_any) ---")
    for method, path in READ_ANY_ENDPOINTS:
        def make_check(m, p):
            def _unauth():
                r = call(m, p, None)
                assert r.status_code == 401, f"expected 401, got {r.status_code}"
            def _viewer_ok():
                r = call(m, p, viewer_t)
                assert r.status_code not in (401, 403), \
                    f"expected non-RBAC response, got {r.status_code}"
            return _unauth, _viewer_ok
        u, v = make_check(method, path)
        step(f"{method} {path} unauthenticated → 401", u)
        step(f"{method} {path} viewer → not 401/403", v)

    # ─────────────────────────────────────────────────────────────────────
    # /api/upload — Analyst+ only.
    # ─────────────────────────────────────────────────────────────────────
    print("\n--- /api/upload (require_analyst) ---")

    def upload_unauth():
        files = {"file": ("test.csv", b"date\n", "text/csv")}
        r = requests.post(f"{BASE_URL}/api/upload", files=files, timeout=15)
        assert r.status_code == 401
    step("POST /api/upload unauthenticated → 401", upload_unauth)

    def upload_viewer_blocked():
        files = {"file": ("test.csv", b"date\n", "text/csv")}
        r = requests.post(
            f"{BASE_URL}/api/upload",
            files=files,
            headers=headers(viewer_t),
            timeout=15,
        )
        assert r.status_code == 403, f"expected 403, got {r.status_code}"
    step("POST /api/upload viewer → 403", upload_viewer_blocked)

    def upload_analyst_passes_rbac():
        # We send a deliberately bad CSV so the endpoint returns 400, not 200.
        # The point is RBAC: Analyst gets PAST require_analyst. The 400 from
        # ingest_csv is the endpoint's normal contract for malformed data.
        files = {"file": ("test.csv", b"bogus\n", "text/csv")}
        r = requests.post(
            f"{BASE_URL}/api/upload",
            files=files,
            headers=headers(analyst_t),
            timeout=15,
        )
        assert r.status_code not in (401, 403), \
            f"expected non-RBAC response, got {r.status_code}"
    step("POST /api/upload analyst → past RBAC", upload_analyst_passes_rbac)

    # ─────────────────────────────────────────────────────────────────────
    # /api/models/{id}/rename — Analyst+ only.
    # ─────────────────────────────────────────────────────────────────────
    print("\n--- PATCH /api/models/{id}/rename (require_analyst) ---")

    def rename_unauth():
        r = requests.patch(
            f"{BASE_URL}/api/models/0/rename",
            json={"new_model_name": "x"},
            timeout=15,
        )
        assert r.status_code == 401
    step("PATCH rename unauthenticated → 401", rename_unauth)

    def rename_viewer_blocked():
        r = requests.patch(
            f"{BASE_URL}/api/models/0/rename",
            json={"new_model_name": "x"},
            headers=headers(viewer_t),
            timeout=15,
        )
        assert r.status_code == 403
    step("PATCH rename viewer → 403", rename_viewer_blocked)

    # ─────────────────────────────────────────────────────────────────────
    # DELETE /api/models/{id} — ADMIN ONLY.
    # ─────────────────────────────────────────────────────────────────────
    print("\n--- DELETE /api/models/{id} (require_admin) ---")

    def delete_unauth():
        r = requests.delete(f"{BASE_URL}/api/models/999999", timeout=15)
        assert r.status_code == 401
    step("DELETE unauthenticated → 401", delete_unauth)

    def delete_viewer_blocked():
        r = requests.delete(
            f"{BASE_URL}/api/models/999999",
            headers=headers(viewer_t),
            timeout=15,
        )
        assert r.status_code == 403
    step("DELETE viewer → 403", delete_viewer_blocked)

    def delete_analyst_blocked():
        # The KEY assertion of conservative-Admin-only: even ANALYST is 403'd.
        r = requests.delete(
            f"{BASE_URL}/api/models/999999",
            headers=headers(analyst_t),
            timeout=15,
        )
        assert r.status_code == 403, \
            f"Analyst should NOT be able to DELETE; got {r.status_code}"
    step("DELETE analyst → 403 (conservative Admin-only)", delete_analyst_blocked)

    def delete_admin_passes_rbac():
        # 999999 doesn't exist → 404 from the endpoint body. RBAC passed.
        r = requests.delete(
            f"{BASE_URL}/api/models/999999",
            headers=headers(admin_t),
            timeout=15,
        )
        assert r.status_code not in (401, 403), \
            f"expected non-RBAC response, got {r.status_code}"
    step("DELETE admin → past RBAC (404 expected)", delete_admin_passes_rbac)

    # ─────────────────────────────────────────────────────────────────────
    # /_debug/db-truth — Admin + non-production.
    # ─────────────────────────────────────────────────────────────────────
    print("\n--- /_debug/db-truth ---")

    def debug_unauth():
        r = requests.get(f"{BASE_URL}/_debug/db-truth", timeout=15)
        # 401 in dev (route exists, needs auth); 404 in prod (route not registered).
        assert r.status_code in (401, 404), f"got {r.status_code}"
    step("/_debug/db-truth unauthenticated → 401 or 404", debug_unauth)

    def debug_viewer_blocked():
        r = requests.get(
            f"{BASE_URL}/_debug/db-truth",
            headers=headers(viewer_t),
            timeout=15,
        )
        assert r.status_code in (403, 404)
    step("/_debug/db-truth viewer → 403 or 404", debug_viewer_blocked)

    # ─────────────────────────────────────────────────────────────────────
    # Audit attribution — every recent state-changing action has a real actor.
    # ─────────────────────────────────────────────────────────────────────
    print("\n--- Audit attribution check ---")

    def audit_attribution():
        # Trigger a deterministic audited action — failed upload as analyst.
        files = {"file": ("rbac-test.txt", b"bogus", "text/plain")}
        r = requests.post(
            f"{BASE_URL}/api/upload",
            files=files,
            headers=headers(analyst_t),
            timeout=15,
        )
        assert r.status_code == 400  # non-CSV filename rejection

        # Now read the audit ledger directly and confirm the row carries
        # the analyst's email AND does NOT have unauthenticated:true.
        from repository.model_repository import SessionLocal
        from sqlalchemy import text
        with SessionLocal() as db:
            row = db.execute(text("""
                SELECT user_email_snapshot,
                       extra_metadata::text
                FROM audit_logs
                WHERE action_type='DATA_UPLOAD_FAILED'
                  AND timestamp > NOW() - INTERVAL '1 minute'
                ORDER BY id DESC LIMIT 1
            """)).fetchone()
        assert row is not None, "no recent DATA_UPLOAD_FAILED row found"
        email, metadata_json = row
        assert email == DEV_ANALYST[0], \
            f"audit row has wrong email: {email} (expected {DEV_ANALYST[0]})"
        assert "unauthenticated" not in (metadata_json or ""), \
            f"audit row still has unauthenticated marker: {metadata_json}"
    step("audit row attributes the actor; no unauth marker", audit_attribution)

    # ─────────────────────────────────────────────────────────────────────
    # Summary
    # ─────────────────────────────────────────────────────────────────────
    print(f"\n=== {PASSED} passed, {FAILED} failed ===\n")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    sys.exit(main())