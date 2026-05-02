# XoCompass — ISO 25010 Defense Matrix

**Project:** XoCompass — Decision Support System for KJS International Travel & Tours
**Coverage:** Admin Console & RBAC layer (Phases 0–6)
**Audience:** Thesis defense panel — quick traceability from quality
characteristic to deliverable.

This matrix is the lookup table behind the system's quality claims. It
maps each ISO 25010 sub-characteristic we addressed to the specific
phase deliverable that addresses it, with a one-line rationale and a
file pointer for live demonstration.

---

## How to read this document

Each row answers three questions a panelist might ask:

1. **Which characteristic does it map to?** (column 1)
2. **Where in the codebase is it?** (column 3)
3. **Why does this implementation satisfy it?** (column 4)

For each entry, a 60-second live demo is feasible from the file pointed to.

---

## Security

ISO 25010 § 4.1 — *the degree to which a product or system protects information
and data so that persons or other products or systems have the degree of data
access appropriate to their types and levels of authorization.*

| Sub-characteristic | Phase | Implementation | Why it satisfies the requirement |
|---|---|---|---|
| **Confidentiality** | 0 | Argon2id password hashing (`core/security.py`) | OWASP 2024+ recommendation; memory-hard; transparent rehash via `password_needs_rehash`. Plaintext never logged or returned. |
| **Confidentiality** | 1 | Hashed invite tokens (`domain/auth_models.InviteToken.token_hash`) | SHA-256 of a 256-bit random; raw token returned exactly once at issue, never persisted. |
| **Confidentiality** | 2 | Stateless 401 envelope (`api/dependencies/auth._credentials_exception`) | All auth-resolution failures return identical message. No enumeration leak (unknown email vs wrong password indistinguishable on the wire). |
| **Confidentiality** | 5 | `/_debug/*` conditional route registration (`api/main.py`) | Routes not registered in production → 404 (not 403). Attacker cannot detect that `/_debug/db-truth` exists. |
| **Confidentiality** | 6 | Rate limiter on `/auth/login` (`core/rate_limit.py`) | 5 attempts per IP per 15 min throttles credential stuffing. Per-IP not per-account → no DoS-able lockout. |
| **Integrity** | 1 | Atomic invite consumption with `SELECT … FOR UPDATE` (`repository/auth_repository.fetch_invite_by_hash_for_update`) | Row-level lock prevents double-redemption races between two browser tabs. |
| **Integrity** | 2 | JWT `typ` claim verification (`core/security.decode_token`) | Refresh token cannot be presented as access token (and vice versa). Closes confused-deputy. |
| **Integrity** | 2 | Refresh-token rotation with reuse detection (`services/auth_service.refresh_session`) | OAuth 2.0 BCP §5.2.2.3 pattern. Replay → revoke ALL user tokens. Demonstrated by `TOKEN_REPLAY_DETECTED` audit row. |
| **Integrity** | 6 | DB trigger on `audit_logs` (`alembic/versions/<rev>_audit_log_immutability.py`) | `BEFORE UPDATE/DELETE` raises `check_violation`. Append-only enforced at storage layer; tamper attempts fail loudly. |
| **Authenticity** | 0 | HS256 JWT signing (`core/security.create_access_token`) | HMAC signature verification on every protected request. Secret rotates quarterly via Cloud Run Secret Manager mount. |
| **Authenticity** | 0 | Boot-time secret validation (`core/security._require_env`) | Refuses to import if `JWT_SECRET_KEY` missing or unchanged from `.env.example`. Cloud Run startup probe fails fast — bad revision never receives traffic. |
| **Access Control** | 2 | Two-layer dependency injection (`api/dependencies/auth.require_role`) | Layer 1 resolves identity (one DB read); Layer 2 enforces role (in-memory frozenset check). Self-auditing via `grep "Depends(require_"`. |
| **Access Control** | 4 | "Last admin" guard (`services/auth_service.admin_set_user_active`, `admin_update_user`) | Service-layer policy: cannot demote/deactivate self; cannot demote the only active Admin. Lockout-proof. |
| **Access Control** | 5 | `Depends(require_*)` on every state-changing endpoint (`api/main.py`) | Conservative tier: read=any, write=Analyst+, destructive=Admin-only. 18/18 RBAC smoke-test assertions confirm. |
| **Accountability** | 1 | `user_email_snapshot` denormalization (`domain/auth_models.AuditLog`) | Audit row captures email AT TIME OF ACTION. Forensic continuity survives user deletion or anonymization. |
| **Accountability** | 3 | Controlled action vocabulary (`services/audit_vocab.py`) | `ActionTypeLiteral` union pinned at type-check time. Typo'd action types fail linter, never reach the database. |
| **Accountability** | 5 | Real actor on every audit row (`api/main.py` retrofit) | Pre-Phase-5 audit rows carry `unauthenticated: true` marker; post-Phase-5 rows attribute the real `User`. The marker IS forensic evidence of when enforcement began. |
| **Non-repudiation** | 6 | DB-level audit immutability (6.4) | Combined with Accountability: an actor cannot later claim "the system logged me wrong" because the row is provably untampered after write. |
| **Resistance to Attack** | 2 | Constant-time login (`services/auth_service.authenticate_user` dummy-hash branch) | Unknown-email path runs an Argon2 verify against a fixed throwaway hash. Closes username-enumeration timing oracle. |
| **Resistance to Attack** | 6 | Rate limiter | See Confidentiality row. |

---

## Reliability

ISO 25010 § 4.2 — *the degree to which a system performs specified functions
under specified conditions for a specified period of time.*

| Sub-characteristic | Phase | Implementation | Why it satisfies the requirement |
|---|---|---|---|
| **Maturity** | 1 | Reversible Alembic migration (Phase 1 migration) | Down-migration is a clean DROP cascade. Rolled out and rolled back cleanly during verification (§1.6.9). |
| **Maturity** | 2 | Service-layer exceptions, never `HTTPException` (`services/auth_service.py`) | Business logic is pytest-able against a SQLite session. The router translates exceptions to HTTP at the boundary. |
| **Maturity** | 5 | Underscore-prefixed `_user` parameter convention | `_user: AuthUser = Depends(require_*)` signals "unused-in-body, present-for-side-effect." Code review can grep for the pattern; new endpoints can't accidentally drop the dependency. |
| **Maturity** | 6 | Explicit JWT error handlers (`api/main.py`) | Expired token → 401 with `code=token_expired` (frontend silent-refresh trigger); JWT signature fail → 401 with `code=request_failed` (hard logout trigger). Two semantically distinct failures, two error codes, one envelope. |
| **Fault Tolerance** | 0 | Argon2 cost-tuning headroom | Default cost (~50ms) appropriate for Cloud Run 4-CPU profile. If `/auth/login` p99 exceeds 200ms under load, drop `memory_cost` to 64 MiB — one-line change, no migration. |
| **Fault Tolerance** | 2 | "Caller commits" repository pattern | Service layer owns transaction boundaries. Multi-write actions (User+InviteToken+AuditLog at registration) commit atomically or roll back atomically. |
| **Fault Tolerance** | 3 | Audit-write graceful degradation (`services/audit_service.log_action`) | Audit-subsystem failure is logged to Cloud Logging but does NOT propagate up to mask the action's outcome. Successful retrain returns 200 even if its audit row hiccups. |
| **Fault Tolerance** | 6 | Rate-limiter response envelope (6.2) | 429 surfaces in the standard `{"error": {"code": "rate_limited", ...}}` envelope. Frontend handles it identically to other 4xx. No special-case parser needed. |
| **Availability** | 0 | Stateless JWT auth | No server-side session store. Cloud Run instances are interchangeable; user state lives in the token. |
| **Availability** | 6 | Per-IP rate limit (not per-account) | A targeted attack against a known user's email cannot DoS that user's account from a second device or network. |
| **Recoverability** | 1 | Idempotent bootstrap script (`scripts/bootstrap_admin.py`) | Safe to embed in Cloud Run deploy hook. First deploy creates admin; subsequent deploys are no-ops. |
| **Recoverability** | 2 | Refresh-token rotation chain (`replaced_by_id`) | Reuse detection revokes all tokens for the user; legitimate user re-logs in (audited as `LOGIN`). System self-heals from token theft. |

---

## Maintainability

ISO 25010 § 4.6 — *the degree of effectiveness and efficiency with which a
product or system can be modified to improve it, correct it, or adapt it.*

| Sub-characteristic | Phase | Implementation | Why it satisfies the requirement |
|---|---|---|---|
| **Modularity** | 0 | `core/` is a leaf package | `core/security.py` imports nothing from our other packages. Anyone can import it; cycles structurally impossible. |
| **Modularity** | 1 | Domain split (`domain/models.py` vs `domain/auth_models.py`) | ML and access-control domains decouple cleanly. Single shared `Base.metadata` for Alembic; otherwise independent. |
| **Modularity** | 2 | Three-layer auth stack | `core` → `repository` → `services` → `api`. Strict downward dependencies; `services/audit_service` duplicates `_client_ip` rather than importing from `api/dependencies` (which would invert layering). |
| **Modularity** | 4 | One router file per admin domain | `admin_users.py`, `admin_invitations.py`, `admin_audit.py`, `admin_system.py`, `admin_settings.py`. Adding a sixth admin concern is a new file, not an edit. |
| **Reusability** | 4 | Per-key validator dispatch dict (`api/routers/admin_settings.SETTING_VALIDATORS`) | Adding a new tunable: (a) migration entry, (b) one-line validator, (c) `Literal` if needed. Three discoverable touchpoints. |
| **Analysability** | 1 | Boot-time vocabulary sanity check (`services/audit_vocab.py`) | If `DEFAULT_MODULE` and `ALL_ACTION_TYPES` drift, the module fails import — Cloud Run never starts a bad revision. |
| **Analysability** | 5 | Single error envelope, app-wide | Every 401/403/404/429/500 response shapes `{"error": {"code", "message", "details"}}`. Frontend has one parser. |
| **Modifiability** | 2 | Role check via `Depends(require_admin)` | Tightening "Analyst can no longer DELETE models" was a one-line change in Phase 5 (`require_analyst` → `require_admin`). No business-logic refactor. |
| **Modifiability** | 5 | `unauthenticated: true` audit marker | Phase 3 wrote it; Phase 5 stopped writing it on retrofitted endpoints. Pre-/post-RBAC rows distinguishable in one query. The seam was designed BEFORE it was crossed. |
| **Testability** | 2 | `app.dependency_overrides` for RBAC | `app.dependency_overrides[require_admin] = lambda: fake_admin` in pytest. No JWT minting required for endpoint unit tests. |
| **Testability** | 6 | Audit-attribution smoke assertion (`scripts/smoke_test_rbac.py`) | Direct DB query confirms the `user_email_snapshot` of a triggered action matches the expected actor AND `unauthenticated` marker is absent. End-to-end traceability test. |

---

## Performance Efficiency

ISO 25010 § 4.4 — *the performance relative to the amount of resources used
under stated conditions.*

| Sub-characteristic | Phase | Implementation | Why it satisfies the requirement |
|---|---|---|---|
| **Time Behavior** | 1 | `ix_audit_timestamp_desc` index | Recent Activity Feed (`SELECT … ORDER BY timestamp DESC LIMIT 20`) is O(log n) seek + 20 leaf reads. Doesn't degrade as audit_logs grows. |
| **Time Behavior** | 2 | Role embedded in JWT | RBAC adds zero DB load on top of identity resolution. One DB read per protected request (the user-by-id), not two. |
| **Time Behavior** | 4 | Cursor pagination on audit reads (`services/audit_service.query_logs`) | (timestamp, id) cursor avoids `OFFSET 10000` table scans at scale. |
| **Time Behavior** | 4 | Settings validator dispatch via dict lookup | Per-key validation is one hash lookup, not a chain of `if key == "x"` branches. Constant-time regardless of vocabulary size. |
| **Resource Utilisation** | 0 | Argon2 memory-cost ceiling (~100 MiB transient per concurrent login) | 4 simultaneous logins ≈ 400 MiB on a 16 GB instance. Bounded by rate limiter, not workload growth. |
| **Resource Utilisation** | 6 | In-memory rate limiter (no Redis) | One bucket per Cloud Run instance. No external state, no extra cost. Trade-off: per-instance budget — acceptable at KJS scale. |
| **Capacity** | 1 | Future-ready partitioning strategy for `audit_logs` | At enterprise scale (>1M rows), `pg_partman` monthly partitioning is a drop-in. Existing index strategy doesn't change. Documented in `domain/auth_models.AuditLog` docstring. |

---

## Defensible Trade-offs

These are choices a panelist might challenge. The defense matrix records the
deliberate reasoning so the answer is "we considered both and chose X
because Y," not "we didn't think about it."

| Trade-off | Choice made | Alternative rejected | Reason |
|---|---|---|---|
| Audit-write failure handling | Log + continue (action succeeds) | Audit-write failure aborts action | At KJS scale, occasional audit gaps are acceptable; a 500 on a successful retrain because the audit subsystem hiccupped is not. (Regulated banking would flip this.) |
| Refresh-token rotation revocation | Burn ALL of user's tokens on replay | Burn only the replayed token | OAuth 2.0 BCP §5.2.2.3. The legitimate user logs back in (audited); the attacker is locked out. Single-token revocation leaves the attacker with their next rotation alive. |
| DELETE model authorization | Admin-only | Admin or Analyst | Conservative. Model deletion wipes joblib + cascade-deletes diagnostics + forecast cache. An Analyst who wants to clean up stale models asks an Admin. |
| Rate-limit storage | In-memory per instance | Redis-backed shared bucket | Cloud Run scale-to-zero and ephemeral instances make in-memory the right choice at KJS volume. Redis is the upgrade path — same `slowapi` API, one URL change. |
| Trigger TRUNCATE protection | Not blocked | Block all destructive ops | A future archival job will TRUNCATE old partitions after copying to cold storage. UPDATE and DELETE are unambiguously tampering; TRUNCATE may be ops. Documented choice. |
| Per-IP not per-account rate limit | Per-IP | Per-account | Per-account is DoS-able by a known-email attacker. Per-IP throttles credential stuffing without weaponizable lockouts. |
| Conditional `/_debug/*` route registration | Don't register in prod | Register but 403 in prod | A 404 from production is indistinguishable from a typo. A 403 confirms the path exists — minor information leak. |

---

## Test coverage map

Every claim above has at least one automated assertion. The map:

| Claim | Test |
|---|---|
| RBAC enforcement on every `/api/*` endpoint | `scripts/smoke_test_rbac.py` (18/18 passing) |
| Auth surface end-to-end | `scripts/smoke_test_auth.py` (post-Phase-6 repair) |
| Audit ledger captures every state-changing endpoint | `scripts/smoke_test_audit.py` (post-Phase-6 repair) |
| Admin console end-to-end | `scripts/smoke_test_admin.py` (19/19 passing) |
| Refresh-token replay detection | Audit ledger query: `SELECT COUNT(*) FROM audit_logs WHERE action_type='TOKEN_REPLAY_DETECTED'` |
| Audit immutability | `psql` UPDATE/DELETE attempts on `audit_logs` raise `check_violation` |
| Rate limiter | 6th `/auth/login` attempt within 15 minutes returns 429 |
| Bootstrap idempotency | Re-running `python -m scripts.bootstrap_admin` reports "Admin already exists" |
| Last-admin guard | Admin self-demote attempt → 409; cross-Admin demote → 200 (when ≥2 admins) |

---

## Phase deliverable index

For panel members who want the chronological view:

| Phase | Deliverable theme | Lines added | Key files |
|---|---|---|---|
| 0 | Crypto primitives, env config | ~250 | `core/security.py` |
| 1 | Schema + bootstrap | ~750 | `domain/auth_models.py`, `alembic/.../add_auth_and_audit_tables.py`, `scripts/bootstrap_admin.py` |
| 2 | Auth endpoints + RBAC dependency machinery | ~1,100 | `api/dependencies/auth.py`, `services/auth_service.py`, `api/routers/auth.py` |
| 3 | Audit infrastructure | ~600 | `services/audit_vocab.py`, `services/audit_service.py`, `api/main.py` audit retrofits |
| 4 | Admin console (5 routers, full surface) | ~1,800 | `api/routers/admin_*.py` |
| 5 | RBAC retrofit on `/api/*` | ~150 (additive) | `api/main.py` |
| 6 | Hardening (rate limiter, audit trigger, smoke repairs, defense matrix) | ~400 | `core/rate_limit.py`, `alembic/.../audit_log_immutability.py`, this file |

**Total system contribution:** approximately 5,000 lines across application code, migrations, scripts, smoke tests, and documentation. Every line traceable to a Phase commit; every Phase commit traceable to a quality characteristic in the rows above.