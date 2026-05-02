# services/audit_vocab.py
"""
The COMPLETE controlled vocabulary for audit_logs.action_type and
audit_logs.module.

DESIGN INVARIANTS:
  1. Every string that ever appears in audit_logs.action_type MUST be
     declared here. Adding a new action requires editing this file —
     which is a deliberate friction. Audit vocabulary is part of the
     system's external contract (compliance, ops dashboards depend on
     it); it does not grow casually.

  2. The Literal[...] aliases are EXPORTED and MUST be the type of every
     `action_type` parameter throughout services/audit_service.py and
     anywhere else audit-row authoring happens. A typo at the call site
     (e.g. "DATA_UPLODED") becomes a type error caught by mypy / Pylance
     before the code even reaches CI.

  3. NEVER add catch-all entries like "OTHER" or "MISC". If you want to
     log something that isn't here, declare a new action_type with a
     name that says what it is.

  4. This module imports nothing from our own codebase. It is a pure
     leaf — domain/, services/, repository/, api/ all import FROM this
     file, not the other way around. ISO 25010 → Maintainability →
     Modularity (no cycles).

WHY A SEPARATE FILE FROM audit_service.py:
  Two concerns, two files. The vocabulary is a STATIC contract; the
  service is a DYNAMIC facade. A frontend dashboard that wants to
  enumerate "all known action types" can `from services.audit_vocab
  import ALL_ACTION_TYPES` without pulling in SQLAlchemy. Phase 4's
  GET /admin/audit-logs/action-types endpoint will use exactly that.
"""

from __future__ import annotations

from typing import Final, Literal, Tuple, get_args


# ═════════════════════════════════════════════════════════════════════════════
# MODULES — coarse-grained domain buckets used in audit_logs.module.
# ═════════════════════════════════════════════════════════════════════════════
#
# A module is the WHERE-IN-THE-SYSTEM of an audit row. It is NOT redundant
# with action_type — many actions can live in the same module (e.g., LOGIN
# and LOGIN_FAILED both live in "auth"). The module exists so that admin
# dashboards can filter "show me everything ingestion-related" without
# enumerating every action_type that touches data.

ModuleLiteral = Literal[
    "auth",            # login, logout, refresh, replay, invite consumption
    "user_management", # admin-initiated user CRUD (Phase 4)
    "ingestion",       # CSV uploads
    "forecast",        # retrain pipeline triggers
    "model_registry",  # model rename / delete
    "settings",        # global_settings PUTs (Phase 4)
    "bootstrap",       # the one-time first-admin script
    "export",          # PDF/data exports (future)
]

ALL_MODULES: Final[Tuple[str, ...]] = get_args(ModuleLiteral)


# ═════════════════════════════════════════════════════════════════════════════
# ACTION TYPES — every string that audit_logs.action_type can ever hold.
# ═════════════════════════════════════════════════════════════════════════════

# ── auth domain ─────────────────────────────────────────────────────────────
AuthActionLiteral = Literal[
    "LOGIN",                  # a user successfully authenticated
    "LOGIN_FAILED",           # bad credentials, inactive account, unknown email
    "LOGOUT",                 # refresh token explicitly revoked
    "TOKEN_REFRESHED",        # refresh token rotated successfully
    "TOKEN_REPLAY_DETECTED",  # consumed refresh token presented again
    "INVITE_CONSUMED",        # invite redeemed via /auth/register
]

# ── user management domain ──────────────────────────────────────────────────
UserMgmtActionLiteral = Literal[
    "USER_CREATED",       # bootstrap or invite consumption
    "USER_DEACTIVATED",   # admin flipped is_active=false (Phase 4)
    "USER_REACTIVATED",   # admin flipped is_active=true (Phase 4)
    "USER_ROLE_CHANGED",  # admin patched role (Phase 4)
    "USER_RENAMED",       # admin patched full_name (Phase 4)
    "INVITE_ISSUED",      # admin called POST /admin/invitations (Phase 4)
    "INVITE_REVOKED",     # admin DELETED a pending invite (Phase 4)
]

# ── data / forecast domain ──────────────────────────────────────────────────
DataActionLiteral = Literal[
    "DATA_UPLOADED",  # CSV ingested via /api/upload
    "DATA_UPLOAD_FAILED",  # CSV upload rejected
    "FORECAST_RUN",       # /api/retrain succeeded — new model registered
    "FORECAST_FAILED",    # /api/retrain failed (orchestrator returned non-zero)
    "MODEL_RENAMED",      # PATCH /api/models/{id}/rename
    "MODEL_DELETED",      # DELETE /api/models/{id}
]

# ── settings & exports (Phase 4+) ───────────────────────────────────────────
SettingsActionLiteral = Literal[
    "SETTINGS_UPDATED",   # PUT /admin/settings/{key}
]

ExportActionLiteral = Literal[
    "EXPORT_GENERATED",   # future PDF/CSV export action
]


# ═════════════════════════════════════════════════════════════════════════════
# UNION — the closed set the audit service accepts.
# ═════════════════════════════════════════════════════════════════════════════
#
# This is the type the audit_service.log_action(action_type=...) parameter
# uses. A call with any string outside this union is a TYPE ERROR.
#
# Example (caught by mypy/Pylance, never reaches runtime):
#     audit_service.log_action(db, action_type="DATA_UPLODED", ...)
#                                                ^^^^^^^^^^^^^
#     # error: Argument "action_type" has incompatible type "Literal['DATA_UPLODED']";
#     #        expected ActionTypeLiteral

ActionTypeLiteral = (
    AuthActionLiteral
    | UserMgmtActionLiteral
    | DataActionLiteral
    | SettingsActionLiteral
    | ExportActionLiteral
)

# Runtime tuple of every accepted value. Useful for:
#   - Phase 4's GET /admin/audit-logs/action-types endpoint.
#   - Tests asserting we haven't drifted between Literal and runtime use.
ALL_ACTION_TYPES: Final[Tuple[str, ...]] = (
    get_args(AuthActionLiteral)
    + get_args(UserMgmtActionLiteral)
    + get_args(DataActionLiteral)
    + get_args(SettingsActionLiteral)
    + get_args(ExportActionLiteral)
)


# ═════════════════════════════════════════════════════════════════════════════
# Default module mapping — the canonical "where does this action live?"
# resolution. Lets callers omit `module=` when the action_type uniquely
# determines it (which is true for every action above).
# ═════════════════════════════════════════════════════════════════════════════

DEFAULT_MODULE: Final[dict[str, str]] = {
    # auth
    "LOGIN": "auth",
    "LOGIN_FAILED": "auth",
    "LOGOUT": "auth",
    "TOKEN_REFRESHED": "auth",
    "TOKEN_REPLAY_DETECTED": "auth",
    "INVITE_CONSUMED": "auth",
    # user_management
    "USER_CREATED": "user_management",
    "USER_DEACTIVATED": "user_management",
    "USER_REACTIVATED": "user_management",
    "USER_ROLE_CHANGED": "user_management",
    "USER_RENAMED": "user_management",
    "INVITE_ISSUED": "user_management",
    "INVITE_REVOKED": "user_management",
    # ingestion
    "DATA_UPLOADED": "ingestion",
    "DATA_UPLOAD_FAILED": "ingestion",
    # forecast
    "FORECAST_RUN": "forecast",
    "FORECAST_FAILED": "forecast",
    # model_registry
    "MODEL_RENAMED": "model_registry",
    "MODEL_DELETED": "model_registry",
    # settings
    "SETTINGS_UPDATED": "settings",
    # export
    "EXPORT_GENERATED": "export",
}

# Defensive sanity check at module import: every action has a default module.
# If we ever add an action_type to ActionTypeLiteral and forget to add it to
# DEFAULT_MODULE, this raises immediately at app boot — never silently
# producing audit rows with a wrong module string.
_missing = set(ALL_ACTION_TYPES) - set(DEFAULT_MODULE)
if _missing:
    raise RuntimeError(
        f"audit_vocab: actions missing from DEFAULT_MODULE: {sorted(_missing)}"
    )

_unknown = set(DEFAULT_MODULE) - set(ALL_ACTION_TYPES)
if _unknown:
    raise RuntimeError(
        f"audit_vocab: DEFAULT_MODULE has unknown actions: {sorted(_unknown)}"
    )


__all__ = [
    "ActionTypeLiteral",
    "ModuleLiteral",
    "ALL_ACTION_TYPES",
    "ALL_MODULES",
    "DEFAULT_MODULE",
]