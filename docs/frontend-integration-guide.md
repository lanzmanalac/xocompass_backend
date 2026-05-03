markdown# XoCompass Backend — Frontend Integration Guide

**Backend version:** Phase 6 (full RBAC + audit + admin console)
**Base URL (local):** `http://127.0.0.1:8000`
**Base URL (production):** `https://xocompass-backend.<region>.run.app`
**OpenAPI explorer:** `<base_url>/docs`

---

## TL;DR for the frontend engineer

1. **Login** with `POST /auth/login`. Save BOTH tokens it returns.
2. **Send `Authorization: Bearer <access_token>`** on every request to `/api/*` and `/admin/*`.
3. **When you get 401 with `code=token_expired`**, call `POST /auth/refresh` once, get new tokens, retry the original request. If `/auth/refresh` also 401s, hard-logout (clear tokens, redirect to login).
4. **Store the user's `role`** from the login response. Use it to show/hide UI for Admin-only / Analyst+ features. The backend STILL enforces RBAC — frontend hiding is UX, not security.
5. **Errors are uniformly shaped** as `{"error": {"code", "message", "details"}}`. Branch on `code`.

That's it. Everything below is detail for specific cases.

---

## 1. Authentication Flow

### 1.1 Login

```http
POST /auth/login
Content-Type: application/json

{
  "email": "alice@kjs.com",
  "password": ""
}
```

**Success (200):**

```json
{
  "access_token": "eyJhbGciOiJI...",
  "refresh_token": "eyJhbGciOiJI...",
  "token_type": "bearer",
  "access_expires_at": "2026-05-02T07:15:00.123456+00:00",
  "refresh_expires_at": "2026-05-09T07:00:00.123456+00:00",
  "user": {
    "id": "f0b54363-b7ca-444e-8ef4-ae4c979477fc",
    "email": "alice@kjs.com",
    "full_name": "Alice Reyes",
    "role": "ADMIN",
    "is_active": true
  }
}
```

**Failure (401):** Wrong email/password OR account deactivated. Same response body for all three — do not branch.

```json
{
  "error": {
    "code": "request_failed",
    "message": "Invalid email or password.",
    "details": []
  }
}
```

**Failure (429):** Too many login attempts from this IP (5 per 15 min).

```json
{
  "error": {
    "code": "rate_limited",
    "message": "Too many requests. Please slow down and try again shortly.",
    "details": []
  }
}
```

### 1.2 Storing tokens

**Recommended:**

| Token | Where to store | Why |
|---|---|---|
| `access_token` | In-memory (React context, Zustand store) | 15-min lifetime; lost on refresh is fine. |
| `refresh_token` | `httpOnly` cookie (preferred) OR `localStorage` (acceptable for v1) | 7-day lifetime; needs to survive browser refresh. |

**Do not** put the access token in `localStorage` — XSS-exposed. **Do not** put the refresh token in JS-accessible storage in production (we'll move to httpOnly cookies in v2).

For v1 thesis defense, `localStorage` for both is acceptable as long as we're not handling real customer financial data yet.

### 1.3 Token refresh (silent)

The access token expires after 15 minutes. The frontend should silent-refresh **slightly before** expiry — a common pattern is to schedule a refresh 60 seconds before `access_expires_at`.

```http
POST /auth/refresh
Content-Type: application/json

{ "refresh_token": "" }
```

**Success (200):**

```json
{
  "access_token": "",
  "refresh_token": "",
  "token_type": "bearer",
  "access_expires_at": "...",
  "refresh_expires_at": "..."
}
```

**CRITICAL:** Both tokens rotate. The OLD refresh token is now dead. **Update your storage with the new refresh_token immediately.** Replaying an old refresh token triggers reuse-detection and revokes ALL the user's tokens — they will be logged out everywhere and have to re-login.

**Failure (401):** Refresh token expired, revoked, or replay detected. Frontend MUST hard-logout (clear tokens, redirect to `/login`).

### 1.4 Logout

```http
POST /auth/logout
Content-Type: application/json
Authorization: Bearer 

{ "refresh_token": "" }
```

Always returns `200` (idempotent). Always clear local storage after the call regardless of response.

```json
{ "status": "ok" }
```

### 1.5 Who am I (bootstrap)

```http
GET /auth/me
Authorization: Bearer 
```

**Success (200):**

```json
{
  "id": "f0b54363-b7ca-444e-8ef4-ae4c979477fc",
  "email": "alice@kjs.com",
  "full_name": "Alice Reyes",
  "role": "ADMIN",
  "is_active": true,
  "last_login_at": "2026-05-02T06:55:00+00:00",
  "created_at": "2026-04-15T03:22:00+00:00"
}
```

Call this on app boot to hydrate the auth context. If it returns 401, the access token has expired — call `/auth/refresh` then retry.

### 1.6 Registration (invite consumption)

When a user clicks an invite link `<frontend_base_url>/register?token=<plaintext_token>`, the frontend extracts the `token` query param and submits:

```http
POST /auth/register
Content-Type: application/json

{
  "invite_token": "",
  "full_name": "Alice Reyes",
  "password": ""
}
```

**Success (201):** Same shape as `/auth/login` response — user is auto-logged-in. Save tokens immediately.

**Failure (400):** Invalid, expired, or already-consumed invite. Tell the user to ask their admin to re-issue.

**Failure (409):** An account already exists for this email. Tell the user to log in instead.

---

## 2. The Universal Error Envelope

Every handled error from this API uses the same shape:

```typescript
interface ErrorEnvelope {
  error: {
    code: string;       // see codes below
    message: string;    // human-readable, safe to display
    details: string[];  // for validation errors, lists field-level issues
  };
}
```

### 2.1 Error codes you will encounter

| `code` | Status | Meaning | Frontend action |
|---|---|---|---|
| `request_failed` | 401 | Auth-resolution failure (no token, bad token, deactivated user) | Try `/auth/refresh`; if that also 401s, hard-logout. |
| `request_failed` | 403 | Authenticated but wrong role | Show "you don't have permission" message. Don't retry. |
| `token_expired` | 401 | Access token explicitly expired | Silent-refresh and retry the original request once. |
| `bad_request` | 400 | Validation failure (look at `details` for field-level issues) | Show inline form errors. |
| `not_found` | 404 | Resource doesn't exist | Show empty-state UI. |
| `rate_limited` | 429 | Too many auth attempts | Show "please wait a few minutes" message. Disable login button. |
| `internal_server_error` | 500 | Server hiccup | Show generic error, offer retry. Log to Sentry/your error tracker. |

### 2.2 The 401-then-refresh recipe

This is the single most important pattern in the integration. Implement it as an axios interceptor (or fetch wrapper) ONCE; every API call benefits.

```typescript
// Pseudo-code. Adapt to your HTTP client.
async function apiCall(url: string, options: RequestInit) {
  const res = await fetch(url, withAuthHeader(options));

  if (res.status !== 401) return res;

  const body = await res.clone().json();

  // Hard 401 (not an expiry) → don't try refresh, just logout.
  if (body?.error?.code !== "token_expired") {
    hardLogout();
    return res;
  }

  // Try silent refresh.
  const refreshed = await trySilentRefresh();
  if (!refreshed) {
    hardLogout();
    return res;
  }

  // Retry the original request with the new token. ONCE.
  return fetch(url, withAuthHeader(options));
}
```

Do NOT retry more than once. If the second attempt also 401s, the user genuinely needs to log in.

---

## 3. Endpoint Reference (by Role)

### 3.1 Public (no token required)

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | API liveness probe |
| GET | `/health/db` | DB connectivity probe |
| POST | `/auth/login` | Authenticate |
| POST | `/auth/refresh` | Rotate tokens |
| POST | `/auth/logout` | Revoke refresh token |
| POST | `/auth/register` | Consume invite, create user |

### 3.2 Any signed-in user (Viewer, Analyst, Admin)

These are the dashboard reads. The frontend calls them once per page load.

| Method | Path | Returns |
|---|---|---|
| GET | `/auth/me` | Current user identity |
| GET | `/api/models` | List of trained models for the dropdown |
| GET | `/api/dashboard-stats/{model_id}` | Page-1 KPI cards |
| GET | `/api/business-analytics` | Tab-1 dataset analytics |
| GET | `/api/historical-data` | Booking ledger time series |
| GET | `/api/forecast-graph/{model_id}` | Actual + predicted overlay |
| GET | `/api/forecast-outlook/{model_id}` | Tab-2 KPI cards + critical weeks |
| GET | `/api/strategic-actions/{model_id}` | Rule-based recommendations |
| GET | `/api/advanced-metrics/{model_id}` | MLOps diagnostics panel |

### 3.3 Analyst or Admin

Hide these UI elements from Viewers based on `user.role`. The backend enforces; frontend just doesn't render the buttons.

| Method | Path | Used by |
|---|---|---|
| POST | `/api/upload` | "Upload CSV" button (Page 3) |
| POST | `/api/retrain` | "Retrain Model" button |
| PATCH | `/api/models/{id}/rename` | Inline rename in the model dropdown |
| GET | `/admin/system/pipeline-status` | Pipeline health badge |
| GET | `/admin/settings` | Read settings (Analyst+) |
| GET | `/admin/settings/{key}` | Read single setting |

### 3.4 Admin only

Hide the entire admin console from non-Admins.

| Method | Path | Used by |
|---|---|---|
| DELETE | `/api/models/{id}` | "Delete model" destructive button |
| GET | `/admin/users` | Admin Console → Users tab |
| GET | `/admin/users/{id}` | User detail drawer |
| PATCH | `/admin/users/{id}` | Edit name / role |
| POST | `/admin/users/{id}/activate` | Activate button |
| POST | `/admin/users/{id}/deactivate` | Deactivate button |
| POST | `/admin/invitations` | "Invite User" form |
| GET | `/admin/invitations` | Pending invites table |
| DELETE | `/admin/invitations/{id}` | Revoke pending invite |
| GET | `/admin/audit-logs` | Audit Log tab (cursor paginated) |
| GET | `/admin/audit-logs/{id}` | Single audit row drawer |
| GET | `/admin/audit-logs/action-types` | Filter dropdown vocab |
| GET | `/admin/system/overview` | Admin dashboard |
| PUT | `/admin/settings/{key}` | Save setting button |

---

## 4. Concrete Recipes for Common UI Flows

### 4.1 App boot (every page load)

Check localStorage for access_token
If absent → redirect to /login
If present → GET /auth/me with the token

200 → hydrate auth context with the response, render the app
401 → try /auth/refresh; if that also 401s, redirect to /login




### 4.2 The dashboard model dropdown

GET /api/models

200 with available_models[] → populate dropdown
404 → show "No models yet. Run /api/retrain after uploading data."


On dropdown change, refetch every model-scoped endpoint with the new ID:
/api/dashboard-stats/{id}, /api/forecast-graph/{id}, etc.


### 4.3 The CSV upload (Analyst+)

Show button only if user.role in ["ADMIN", "ANALYST"]
On submit, POST /api/upload with multipart/form-data

200 status="success" → toast "Ingested N records"; refresh dataset views
200 status="skipped" → toast "No new records (duplicate dates)"
400 → inline error from response.error.message
500 → toast "Upload failed; try again or contact support"


After success, optionally trigger POST /api/retrain to update forecasts


### 4.4 The retrain trigger (Analyst+)

Show button only if user.role in ["ADMIN", "ANALYST"]
On click, POST /api/retrain with the chosen config
This is a LONG request (up to 25 minutes timeout). Show a spinner.
Consider polling /admin/system/pipeline-status every 30 seconds
to update the progress UI.
200 → refresh model dropdown; new model is now active
500 → show the error message; the audit log will have details


### 4.5 The admin console (Admin)

Hide entire console route from non-Admins. Backend enforces, but
frontend should not even show the menu item.
Users tab: GET /admin/users with ?role=&status=&search=&page= filters
Invitations tab:

POST /admin/invitations → response includes invite_url; show in a
"copy this link to the invitee" modal. The plaintext token appears
ONLY in this response — there is NO endpoint to retrieve it later.
GET /admin/invitations to show pending/consumed/expired


Audit tab: GET /admin/audit-logs?cursor=<previous next_cursor>&limit=50

Cursor pagination. Pass back next_cursor on scroll to load more.
First page: omit the cursor parameter.


Settings tab: GET /admin/settings, then PUT /admin/settings/{key}
on save. Per-key validation is server-side; you'll see 400 with a
clear message if the value is out of range.


---

## 5. TypeScript Types (Generated from OpenAPI)

The backend's OpenAPI spec is the contract. Generate types automatically:

```bash
# Install the generator
npm install -D openapi-typescript

# Generate types from the running backend
npx openapi-typescript http://127.0.0.1:8000/openapi.json -o src/types/api.ts
```

This produces `src/types/api.ts` with every response and request shape pre-typed. Re-run the generator whenever the backend ships a new endpoint.

For Phase 6, the most-used types will be:

```typescript
import type { paths, components } from "@/types/api";

type LoginResponse = components["schemas"]["LoginResponse"];
type AuthenticatedUser = components["schemas"]["AuthenticatedUser"];
type DashboardStatsResponse = components["schemas"]["DashboardStatsResponse"];
type ForecastGraphResponse = components["schemas"]["ForecastGraphResponse"];
type AuditLogPageResponse = components["schemas"]["AuditLogPageResponse"];
// ...
```

---

## 6. Environment Configuration

Frontend `.env.local`:

```env
# Local development
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000

# When deployed, this becomes the Cloud Run URL:
# NEXT_PUBLIC_API_BASE_URL=https://xocompass-backend.<region>.run.app
```

The backend's `FRONTEND_BASE_URL` env var (which it uses to build invite URLs) must match the frontend's deployment URL. Currently:

| Environment | Backend FRONTEND_BASE_URL | Frontend NEXT_PUBLIC_API_BASE_URL |
|---|---|---|
| Local dev | `http://localhost:3000` | `http://127.0.0.1:8000` |
| Production | `https://xocompass.vercel.app` | `https://xocompass-backend.<region>.run.app` |

---

## 7. CORS

The backend already allows the standard origins:

- `http://localhost:3000` (Next.js default)
- `http://localhost:5173` (Vite default)
- `https://xocompass.vercel.app` (production)

If your dev server runs on a different port, ask the backend engineer to add it to `CORS_ALLOWED_ORIGINS`. **Do not** try to bypass with a CORS proxy — the backend supports credentials, which CORS proxies break.

---

## 8. Common Gotchas

### 8.1 Tokens rotate on refresh — UPDATE STORAGE

The single most common integration bug. After `POST /auth/refresh`, BOTH `access_token` AND `refresh_token` change. If your code only updates the access token and keeps using the old refresh token, the next refresh will trigger replay-detection and the user will be logged out everywhere.

```typescript
// WRONG
async function refresh() {
  const { access_token } = await callRefresh();
  setAccessToken(access_token);
  // BUG: refresh_token in storage is now dead
}

// RIGHT
async function refresh() {
  const { access_token, refresh_token } = await callRefresh();
  setAccessToken(access_token);
  setRefreshToken(refresh_token);  // CRITICAL
}
```

### 8.2 The plaintext invite token appears EXACTLY ONCE

The `POST /admin/invitations` response contains `invite_url` with the plaintext token embedded in the query string. **There is no endpoint to retrieve this URL again.** If the admin closes the modal without copying it, they must DELETE the invitation and create a new one.

UX recommendation: show the URL in a read-only input with a "Copy to clipboard" button, and have a confirmation modal ("Did you copy the link?") before allowing the admin to dismiss.

### 8.3 Audit log filters use enum-cased values

`GET /admin/audit-logs?action_type=LOGIN_FAILED&module=auth&status=FAILED` — the values are case-sensitive. `action_type=login_failed` returns no rows.

The full vocabulary is at `GET /admin/audit-logs/action-types`. Use that response to populate filter dropdowns; never hard-code.

### 8.4 Date filters expect ISO 8601

`?from_date=2026-04-01T00:00:00Z&to_date=2026-04-30T23:59:59Z` — full ISO strings with timezone. The backend uses Asia/Manila (UTC+8) for display but stores everything UTC; pass UTC to the API.

### 8.5 The dashboard requires a model_id; show empty state when none exist

On a fresh deploy with no models trained yet, `GET /api/models` returns 404. The frontend should show:

> "No models yet. Upload a CSV via Page 3, then run a retrain."

Do not show a broken dashboard with `Model #undefined`.

### 8.6 Role changes don't take effect for up to 15 minutes

If an Admin changes a user's role, the user keeps their OLD role until their access token expires (15 minutes max) and they refresh. This is the documented trade-off for stateless JWT auth — the alternative would be a per-request DB hit on every endpoint, which we explicitly chose against.

If a user reports "I can't see the new feature my admin granted me," tell them to log out and log back in. We may add a "force re-login" admin action in a future phase.

### 8.7 Deactivation IS instant for refresh; access token still lives 15 min

When an admin deactivates a user, ALL the user's refresh tokens are revoked immediately. They cannot get a new access token. Their CURRENT access token, however, is still valid until expiry (≤15 min).

This is the right trade-off for "user left the company": their session dies within 15 min worst case, all their persistent sessions die immediately.

---

## 9. Live Documentation

Always-on, always-current API documentation:

- **Swagger UI:** `<base_url>/docs` — interactive, "Authorize" button accepts a Bearer token, you can try every endpoint live.
- **ReDoc:** `<base_url>/redoc` — read-only, prettier for sharing.
- **OpenAPI JSON:** `<base_url>/openapi.json` — feed this to `openapi-typescript` for code generation.

If the docs ever disagree with this guide, the docs are right (they're generated from the source). File an issue against this guide and we'll update it.

---

## 10. Contact / Iteration

If something in the API doesn't fit a frontend use case cleanly:

1. **Don't work around it on the frontend.** Document the friction.
2. **Ask the backend engineer (Leap)** — most can be fixed with a thin adapter endpoint or a query parameter.
3. **For breaking contract changes**, we'll cut a v2 endpoint rather than mutate v1.

The backend prioritizes:
- ISO 25010 quality characteristics (security, reliability, maintainability)
- Stable wire contracts (we don't break shipped endpoints)
- Auditability (every state change is recorded)

If a frontend need conflicts with one of these, we negotiate; we don't override silently.