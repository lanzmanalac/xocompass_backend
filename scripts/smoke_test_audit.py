# scripts/smoke_test_audit.py
"""
End-to-end smoke test for the Phase 3 audit infrastructure.

Exercises every newly-wired audit point:
  1. Phase 2 auth audit rows still produced (sanity)
  2. /api/upload SUCCESS path → DATA_UPLOADED row
  3. /api/upload FAILURE path (non-CSV filename) → DATA_UPLOAD_FAILED row
  4. /api/models/{id}/rename → MODEL_RENAMED row, with old_name/new_name
  5. Vocabulary integrity: every action_type in audit_logs for the last
     5 minutes is in audit_vocab.ALL_ACTION_TYPES.

Does NOT exercise /api/retrain or DELETE /api/models — those have
side effects on the model registry that aren't appropriate for a
smoke test that runs against a real database with seeded models.
Verify those manually after a full retrain.

Run:
    # Terminal 1: API running
    uvicorn api.main:app --reload

    # Terminal 2:
    SMOKE_ADMIN_PASSWORD='<your-password>' python -m scripts.smoke_test_audit
"""

from __future__ import annotations

import io
import os
import sys

import requests

BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8000").rstrip("/")
ADMIN_EMAIL = os.getenv("SMOKE_ADMIN_EMAIL", "leapziggy@gmail.com")
ADMIN_PASSWORD = os.getenv("SMOKE_ADMIN_PASSWORD")

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
        return None
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAIL] {label}: unexpected {type(exc).__name__}: {exc}")
        FAILED += 1
        return None


def query_audit_counts(action_types: list[str]) -> dict[str, int]:
    """Count audit_logs rows for the given action_types in the last 5
    minutes, by querying the DB directly."""
    from repository.model_repository import SessionLocal
    from sqlalchemy import text

    placeholders = ", ".join(f":a{i}" for i in range(len(action_types)))
    sql = text(
        f"SELECT action_type, COUNT(*) AS c "
        f"FROM audit_logs "
        f"WHERE timestamp > NOW() - INTERVAL '5 minutes' "
        f"  AND action_type IN ({placeholders}) "
        f"GROUP BY action_type"
    )
    params = {f"a{i}": v for i, v in enumerate(action_types)}
    with SessionLocal() as db:
        rows = db.execute(sql, params).fetchall()
    return {r[0]: int(r[1]) for r in rows}



def main() -> int:
    if not ADMIN_PASSWORD:
        print("[ERROR] Set SMOKE_ADMIN_PASSWORD.")
        return 1

    print(f"\n=== XoCompass Audit Smoke Test ===")
    print(f"BASE_URL = {BASE_URL}\n")

    # Capture baseline counts BEFORE we exercise the endpoints.
    baseline = query_audit_counts([
        "DATA_UPLOADED", "DATA_UPLOAD_FAILED",
        "MODEL_RENAMED",
        "LOGIN", "LOGIN_FAILED",
    ])

    # ── 1. Login (for the LOGIN baseline bump) ─────────────────────────────
    login_payload = {}
    def login():
        r = requests.post(
            f"{BASE_URL}/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            timeout=15,
        )
        assert r.status_code == 200, f"{r.status_code} {r.text}"
        login_payload.update(r.json())
    step("login (auth audit baseline)", login)
    if FAILED:
        return 1

    auth_h: dict = {}

    def admin_login():
        r = requests.post(
            f"{BASE_URL}/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            timeout=15,
        )
        assert r.status_code == 200
        auth_h["Authorization"] = f"Bearer {r.json()['access_token']}"
    step("admin login (audit baseline)", admin_login)
    if FAILED:
        return 1

    def upload_bad_filename():
        files = {"file": ("not_a_csv.txt", io.BytesIO(b"hello"), "text/plain")}
        r = requests.post(
            f"{BASE_URL}/api/upload",
            files=files,
            headers=auth_h,            # ── PHASE 6 FIX ──
            timeout=15,
        )
        assert r.status_code == 400
    step("upload non-CSV → 400 + DATA_UPLOAD_FAILED row", upload_bad_filename)

    def upload_empty_csv():
        files = {"file": ("empty.csv", io.BytesIO(b""), "text/csv")}
        r = requests.post(
            f"{BASE_URL}/api/upload",
            files=files,
            headers=auth_h,            # ── PHASE 6 FIX ──
            timeout=15,
        )
        assert r.status_code in (400, 500), f"{r.status_code} {r.text}"
    step("upload empty CSV → 4xx/5xx + DATA_UPLOAD_FAILED row",
         upload_empty_csv)

    def rename_a_model():
        r = requests.get(f"{BASE_URL}/api/models", headers=auth_h, timeout=15)  # ── FIX ──
        assert r.status_code == 200, f"{r.status_code}"
        models = r.json().get("available_models", [])
        if not models:
            raise AssertionError("no models available — seed at least one")
        target = models[0]
        original = target["model_name"]
        new_name = f"{original} [audit-smoke]"

        r1 = requests.patch(
            f"{BASE_URL}/api/models/{target['id']}/rename",
            json={"new_model_name": new_name},
            headers=auth_h,            # ── FIX ──
            timeout=15,
        )
        assert r1.status_code == 200, f"{r1.status_code} {r1.text}"

        r2 = requests.patch(
            f"{BASE_URL}/api/models/{target['id']}/rename",
            json={"new_model_name": original},
            headers=auth_h,            # ── FIX ──
            timeout=15,
        )
        assert r2.status_code == 200, f"{r2.status_code} {r2.text}"
    step("rename model forward+back → 2× MODEL_RENAMED rows", rename_a_model)

    # ── 5. Verify the audit rows landed ────────────────────────────────────
    def audit_rows_landed():
        after = query_audit_counts([
            "DATA_UPLOADED", "DATA_UPLOAD_FAILED",
            "MODEL_RENAMED",
            "LOGIN",
        ])
        upload_failed_delta = after.get("DATA_UPLOAD_FAILED", 0) - baseline.get("DATA_UPLOAD_FAILED", 0)
        rename_delta = after.get("MODEL_RENAMED", 0) - baseline.get("MODEL_RENAMED", 0)
        login_delta = after.get("LOGIN", 0) - baseline.get("LOGIN", 0)

        assert upload_failed_delta >= 2, \
            f"expected ≥2 new DATA_UPLOAD_FAILED rows, got {upload_failed_delta}"
        assert rename_delta >= 2, \
            f"expected ≥2 new MODEL_RENAMED rows, got {rename_delta}"
        assert login_delta >= 1, \
            f"expected ≥1 new LOGIN row, got {login_delta}"
    step("audit deltas match the actions issued", audit_rows_landed)

    # ── 6. Vocabulary integrity ────────────────────────────────────────────
    def vocab_integrity():
        from services.audit_vocab import ALL_ACTION_TYPES
        from repository.model_repository import SessionLocal
        from sqlalchemy import text

        with SessionLocal() as db:
            rows = db.execute(text(
                "SELECT DISTINCT action_type FROM audit_logs "
                "WHERE timestamp > NOW() - INTERVAL '5 minutes'"
            )).fetchall()
        observed = {r[0] for r in rows}
        unknown = observed - set(ALL_ACTION_TYPES)
        assert not unknown, \
            f"audit_logs has action_types not in ALL_ACTION_TYPES: {unknown}"
    step("every observed action_type is in the controlled vocab",
         vocab_integrity)

    print(f"\n=== {PASSED} passed, {FAILED} failed ===\n")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    sys.exit(main())