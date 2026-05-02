# scripts/bootstrap_admin.py
"""
Bootstrap the first Admin user.

CHICKEN-AND-EGG PROBLEM:
  Phase 4 will lock the /admin/users and /admin/invitations endpoints
  behind `require_admin` (Phase 2). Until at least one Admin row exists,
  nobody can call them — and there's no admin endpoint to create one.

THIS SCRIPT IS THE ONLY WAY TO CREATE AN ADMIN OUTSIDE THE INVITE FLOW.
After it runs once, every subsequent user is provisioned via the normal
invite flow (POST /admin/invitations → POST /auth/register).

USAGE:
    # Interactive (recommended for local/staging):
    python -m scripts.bootstrap_admin

    # Non-interactive (one-shot Cloud Run job):
    BOOTSTRAP_ADMIN_EMAIL="alice@kjs.com" \
    BOOTSTRAP_ADMIN_NAME="Alice Reyes" \
    BOOTSTRAP_ADMIN_PASSWORD="<paste-strong-password>" \
        python -m scripts.bootstrap_admin --non-interactive

IDEMPOTENCY:
  The script SKIPS creation if any Admin row already exists. Running it
  twice is a no-op with an informative message. This means it is safe to
  bake into a Cloud Run "Job" that runs on every deploy — first deploy
  creates the admin, every subsequent deploy is harmless.

SAFETY:
  - Never echoes the password.
  - Refuses passwords shorter than 12 chars.
  - Refuses to run in a non-development environment without explicit
    --i-am-aware-this-is-production confirmation.
  - Logs the creation as an AuditLog row with action_type=USER_CREATED
    and a special module=bootstrap so it is distinguishable from
    admin-issued invites.

EXIT CODES:
  0  Admin created OR an Admin already existed (idempotent success).
  1  Validation failure (bad input, weak password, environment refusal).
  2  Database failure (migration not applied, connection refused).
"""

from __future__ import annotations

import argparse
import getpass
import logging
import os
import re
import sys
from typing import NoReturn

# IMPORTANT: do not import anything that triggers core.security at the top
# level, so that a missing JWT_SECRET_KEY does not crash the script before
# we have a chance to print a helpful diagnostic. We import lazily below.

logger = logging.getLogger("bootstrap_admin")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LEN = 12


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _die(msg: str, code: int = 1) -> NoReturn:
    print(f"\n[ERROR] {msg}\n", file=sys.stderr)
    sys.exit(code)


def _validate_email(email: str) -> str:
    email = email.strip().lower()
    if not EMAIL_RE.match(email):
        _die(f"Invalid email format: {email!r}")
    return email


def _validate_full_name(name: str) -> str:
    name = name.strip()
    if not name:
        _die("Full name cannot be empty.")
    if len(name) > 120:
        _die("Full name exceeds the 120-character schema limit.")
    return name


def _validate_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LEN:
        _die(
            f"Password must be at least {MIN_PASSWORD_LEN} characters. "
            f"Use a passphrase, e.g. 'correct horse battery staple'."
        )
    if password.lower() in {"password" * 2, "admin" * 3, "12345678" + "9012"}:
        _die("Password is in a known weak-password list.")
    return password


def _confirm_environment(non_interactive: bool, force_prod: bool) -> None:
    env = os.getenv("ENVIRONMENT", "development").strip().lower()
    if env in ("development", "staging", ""):
        return
    # In 'production', refuse without explicit confirmation.
    if force_prod:
        logger.warning("Bootstrapping admin in PRODUCTION (forced).")
        return
    if non_interactive:
        _die(
            "ENVIRONMENT=production and --non-interactive set, but "
            "--i-am-aware-this-is-production was NOT passed. Refusing.",
        )
    confirm = input(
        f"\n[WARNING] ENVIRONMENT={env}. "
        f"Type 'yes-bootstrap-prod' to proceed: "
    ).strip()
    if confirm != "yes-bootstrap-prod":
        _die("Aborted by operator.")


# ─────────────────────────────────────────────────────────────────────────────
# Main flow
# ─────────────────────────────────────────────────────────────────────────────


def _read_inputs(non_interactive: bool) -> tuple[str, str, str]:
    if non_interactive:
        email = os.getenv("BOOTSTRAP_ADMIN_EMAIL", "")
        name = os.getenv("BOOTSTRAP_ADMIN_NAME", "")
        password = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "")
        if not (email and name and password):
            _die(
                "Non-interactive mode requires BOOTSTRAP_ADMIN_EMAIL, "
                "BOOTSTRAP_ADMIN_NAME, and BOOTSTRAP_ADMIN_PASSWORD env vars."
            )
        return _validate_email(email), _validate_full_name(name), _validate_password(password)

    print("\n=== XoCompass Bootstrap Admin ===\n")
    email = _validate_email(input("Admin email: "))
    name = _validate_full_name(input("Full name: "))

    # getpass.getpass DOES NOT echo. Two-shot confirmation prevents typos
    # locking out the operator.
    pw1 = getpass.getpass("Password (min 12 chars): ")
    pw2 = getpass.getpass("Confirm password: ")
    if pw1 != pw2:
        _die("Passwords do not match.")
    return email, name, _validate_password(pw1)


def _bootstrap(email: str, full_name: str, password: str) -> int:
    # Imports are deliberately deferred until AFTER input validation, so
    # that `python -m scripts.bootstrap_admin` with an unset JWT_SECRET_KEY
    # does not crash before we've asked for the password. The Phase 0
    # _require_env guard is still load-bearing — we just want to surface
    # it as an actionable error message, not a stack trace.
    try:
        from core.security import hash_password
    except RuntimeError as exc:
        _die(
            f"core.security failed to import: {exc}\n"
            f"Set JWT_SECRET_KEY (see .env.example) and try again.",
            code=1,
        )

    try:
        from repository.model_repository import SessionLocal
        from domain.auth_models import (
            User,
            UserRole,
            AuditLog,
            AuditStatus,
        )
    except Exception as exc:  # noqa: BLE001 — script entrypoint
        _die(f"Could not import database layer: {exc}", code=2)

    session = SessionLocal()
    try:
        # ── Idempotency check ───────────────────────────────────────────
        existing_admin = (
            session.query(User).filter(User.role == UserRole.ADMIN).first()
        )
        if existing_admin is not None:
            print(
                f"\n[OK] An Admin already exists: "
                f"{existing_admin.email} (id={existing_admin.id}). "
                f"Bootstrap is idempotent — skipping.\n"
            )
            return 0

        # ── Email collision check (e.g., a Viewer was somehow created) ──
        clash = session.query(User).filter(User.email == email).first()
        if clash is not None:
            _die(
                f"A user with email {email!r} already exists with role "
                f"{clash.role.value}. Cannot bootstrap. Resolve manually.",
                code=1,
            )

        # ── Create the user atomically with its audit row ───────────────
        admin = User(
            email=email,
            full_name=full_name,
            hashed_password=hash_password(password),
            role=UserRole.ADMIN,
            is_active=True,
            created_by_user_id=None,  # bootstrap admin has no provisioner
        )
        session.add(admin)
        session.flush()  # assign admin.id without committing

        session.add(
            AuditLog(
                user_id=admin.id,
                user_email_snapshot=admin.email,
                action_type="USER_CREATED",
                module="bootstrap",  # distinguishable from admin-issued invites
                target_resource=f"user_id={admin.id}",
                status=AuditStatus.SUCCESS,
                ip_address=None,
                user_agent="bootstrap_admin.py",
                extra_metadata={
                    "method": "bootstrap_script",
                    "role": UserRole.ADMIN.value,
                },
            )
        )

        session.commit()

        print(
            f"\n[OK] Admin created.\n"
            f"     id:    {admin.id}\n"
            f"     email: {admin.email}\n"
            f"     role:  {admin.role.value}\n"
            f"\nYou can now log in via POST /auth/login (Phase 2).\n"
        )
        return 0

    except Exception as exc:  # noqa: BLE001 — script entrypoint
        session.rollback()
        # Surface the underlying class name; full traceback at DEBUG.
        logger.exception("Bootstrap failed.")
        _die(f"Bootstrap failed: {type(exc).__name__}: {exc}", code=2)
    finally:
        session.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(
        description="Create the first XoCompass Admin user. Idempotent."
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Read inputs from BOOTSTRAP_ADMIN_* env vars instead of prompting.",
    )
    parser.add_argument(
        "--i-am-aware-this-is-production",
        dest="force_prod",
        action="store_true",
        help="Bypass the production safety prompt. Use only in deploy jobs.",
    )
    args = parser.parse_args()

    _confirm_environment(args.non_interactive, args.force_prod)
    email, full_name, password = _read_inputs(args.non_interactive)
    sys.exit(_bootstrap(email, full_name, password))


if __name__ == "__main__":
    main()