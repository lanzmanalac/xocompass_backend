# Frontend API Guide

## Scope

Smoke-tested on 2026-04-15 against the local SQLite dataset in `xocompass.db`.

For offline/local checks, override the checked-in Neon connection string:

```bash
DATABASE_URL=sqlite:///./xocompass.db .venv/bin/python -m uvicorn api.main:app --reload
```

## Authentication & Authorization (Phase 5+)

As of Phase 5, every `/api/*` endpoint requires a valid Bearer token.
The `/health*` endpoints and `/docs`/`/openapi.json` remain public.

### Login → Token

```http
POST /auth/login
Content-Type: application/json

{ "email": "<user>", "password": "<password>" }
```

Successful response includes `access_token`, `refresh_token`, both
expiry timestamps, and the user's `role` ("ADMIN" | "ANALYST" | "VIEWER").

### Authenticated requests

Every `/api/*` and `/admin/*` request MUST carry:
Authorization: Bearer <access_token>

### Role requirements per route

| Endpoint                            | Method | Required role           |
|-------------------------------------|--------|-------------------------|
| `/api/models`                       | GET    | any signed-in user      |
| `/api/dashboard-stats/{id}`         | GET    | any signed-in user      |
| `/api/business-analytics`           | GET    | any signed-in user      |
| `/api/advanced-metrics/{id}`        | GET    | any signed-in user      |
| `/api/forecast-outlook/{id}`        | GET    | any signed-in user      |
| `/api/forecast-graph/{id}`          | GET    | any signed-in user      |
| `/api/strategic-actions/{id}`       | GET    | any signed-in user      |
| `/api/historical-data`              | GET    | any signed-in user      |
| `/api/upload`                       | POST   | Admin or Analyst        |
| `/api/retrain`                      | POST   | Admin or Analyst        |
| `/api/models/{id}/rename`           | PATCH  | Admin or Analyst        |
| `/api/models/{id}`                  | DELETE | **Admin only**          |
| `/admin/*`                          | any    | Admin only (read settings: Analyst+) |

### Failure modes

- Missing or invalid token → `401` with `{"error":{"code":"request_failed","message":"Could not validate credentials.","details":[]}}`
- Valid token, wrong role → `403` with `{"error":{"code":"request_failed","message":"Insufficient permissions for this operation.","details":[]}}`
- Expired access token → `401`. The frontend should call `POST /auth/refresh` with the stored refresh token and retry the original request once.

### Refresh flow

```http
POST /auth/refresh
{ "refresh_token": "<refresh_token>" }
```

Returns a new pair of (access, refresh) tokens. **The old refresh token becomes invalid** — store the new one immediately. Replaying the old refresh token will trigger reuse-detection: ALL refresh tokens for the user are revoked, and the user must log in again.


Notes:

- The repo `.env` currently points to Neon/Postgres.
- In restricted or offline environments, DB-backed routes will fail unless `DATABASE_URL` is overridden to the local SQLite file.
- Contract checks in this pass were run through the FastAPI app directly, which exercises the same routing, validation, and response serialization as HTTP.

## Shared Conventions

- Success responses are JSON objects.
- Dates are ISO 8601 datetime strings.
- Numeric values are returned as JSON numbers, not strings.
- Nullable chart fields are returned as `null`, not omitted.

All handled errors use the same envelope:

```json
{
  "error": {
    "code": "bad_request",
    "message": "Request validation failed.",
    "details": [
      "body.model_selection: Input should be 'ARIMA', 'SARIMA' or 'SARIMAX'"
    ]
  }
}
```

Observed error codes:

- `bad_request`: request validation or upload validation failures
- `not_found`: missing routes or missing resources
- `internal_server_error`: database outages or unexpected server failures

## Verified Endpoints

### `GET /health`

Purpose: basic API liveness check.

```json
{
  "status": "ok",
  "service": "api"
}
```

### `GET /health/db`

Purpose: confirms the configured database is reachable.

Success:

```json
{
  "status": "ok",
  "service": "api",
  "database": "connected"
}
```

If the DB is unavailable:

```json
{
  "error": {
    "code": "internal_server_error",
    "message": "Database connectivity check failed.",
    "details": []
  }
}
```

### `GET /api/models`

Purpose: model dropdown source.

Success shape:

```json
{
  "available_models": [
    {
      "id": 1,
      "version": "v10.1",
      "train_end_date": "2025-12-29T00:00:00",
      "aic_score": 204.3,
      "notes": null
    }
  ]
}
```

Frontend notes:

- `notes` is optional and currently comes back as `null` in the seeded dataset.
- `train_end_date` is nullable.

Empty state:

```json
{
  "error": {
    "code": "not_found",
    "message": "No trained models found.",
    "details": []
  }
}
```

### `GET /api/dashboard-stats/{model_id}`

Purpose: executive dashboard cards plus temporary chart data.

Success shape:

```json
{
  "total_records": 104,
  "data_quality_pct": 98.5,
  "revenue_total": 2450000.0,
  "growth_rate": 12.5,
  "expected_bookings": 450,
  "peak_travel_period": "April - May 2026",
  "bookings_forecast": [
    {
      "month": "Jan",
      "actual": 280.0,
      "predicted": 295.0,
      "lowerCI": 260.0,
      "upperCI": 330.0
    }
  ]
}
```

Frontend notes:

- `bookings_forecast` is temporary mock chart data for integration.
- The chart keys are `lowerCI` and `upperCI`, not `lower_bound` and `upper_bound`.

Missing snapshot:

```json
{
  "error": {
    "code": "not_found",
    "message": "Snapshot not found for this model.",
    "details": []
  }
}
```

### `GET /api/advanced-metrics/{model_id}`

Purpose: MLOps / diagnostics panel.

Success shape:

```json
{
  "model_params": {
    "order": [2, 1, 2],
    "seasonal_order": [1, 1, 1],
    "exogenous_features": ["holiday_lead_2", "is_long_weekend"]
  },
  "statistics": {
    "rmse": 12.4,
    "mae": 9.8,
    "wmape": 0.01232
  },
  "statistical_tests": {
    "adf_stat": -3.45,
    "adf_pvalue": 0.008,
    "adf_conclusion": "Pending",
    "ljungbox_stat": 0.0,
    "ljungbox_pvalue": 0.45,
    "ljungbox_conclusion": "Pending",
    "jarquebera_stat": 0.0,
    "jarquebera_pvalue": 0.32,
    "jarquebera_conclusion": "Pending"
  },
  "charts": {
    "residuals": [
      { "fitted": 100.0, "residual": -2.0 }
    ],
    "acf": [
      { "lag": 0, "value": 1.0 }
    ],
    "pacf": [
      { "lag": 0, "value": 1.0 }
    ]
  }
}
```

Frontend notes:

- `seasonal_order` currently returns three values `[P, D, Q]`, not a four-part SARIMAX seasonal tuple.
- The API now normalizes both legacy float arrays and `{lag, value}` arrays for `acf` and `pacf`, so the frontend can always expect object arrays.
- Several statistical-test fields may fall back to `0.0` or `"Pending"` when DB values are null.

Missing model:

```json
{
  "error": {
    "code": "not_found",
    "message": "Model 1 not found.",
    "details": []
  }
}
```

### `GET /api/historical-data`

Purpose: historical ledger / training data view.

Success shape:

```json
{
  "data": [
    {
      "date": "2026-02-01T01:16:07.862496",
      "bookings": 120.0,
      "is_holiday": true,
      "weather_indicator": 0.0
    }
  ]
}
```

Empty state:

```json
{
  "error": {
    "code": "not_found",
    "message": "No historical booking data found.",
    "details": []
  }
}
```

### `GET /api/forecast-graph/{model_id}`

Purpose: combined actual-vs-predicted series for the dashboard graph.

Success shape:

```json
{
  "data": [
    {
      "date": "2026-02-01T01:16:07.862496",
      "actual": 120.0,
      "predicted": null,
      "lower_bound": null,
      "upper_bound": null
    },
    {
      "date": "2026-04-19T01:16:07.862496",
      "actual": null,
      "predicted": 150.0,
      "lower_bound": 130.0,
      "upper_bound": 170.0
    }
  ]
}
```

Frontend notes:

- This is a mixed timeline.
- Historical points have `actual` populated and prediction fields as `null`.
- Forecast points have `predicted` and bounds populated and `actual` as `null`.

Missing model:

```json
{
  "error": {
    "code": "not_found",
    "message": "Model 999 not found.",
    "details": []
  }
}
```

### `GET /api/strategic-actions/{model_id}`

Purpose: rule-based recommendations derived from forecast cache.

Success shape:

```json
{
  "actions": [
    {
      "priority": "LOW",
      "category": "Pricing",
      "action": "Review base pricing quarterly - upward booking trend supports gradual rate increases.",
      "trigger": "16-week forecast shows positive demand trajectory."
    }
  ],
  "generated_for_period": "April 2026 - May 2026"
}
```

Frontend notes:

- `actions` can be an empty array if no rules are triggered.
- `generated_for_period` is always a string.

Missing model:

```json
{
  "error": {
    "code": "not_found",
    "message": "Model 1 not found.",
    "details": []
  }
}
```

### `POST /api/upload`

Purpose: ingest a CSV and create new weekly records.

Success:

```json
{
  "status": "success",
  "message": "Ingested 2 new weekly records.",
  "new_records": 2
}
```

Duplicate upload / no-op:

```json
{
  "status": "skipped",
  "message": "No new records.",
  "new_records": 0
}
```

Validation failure:

```json
{
  "error": {
    "code": "bad_request",
    "message": "Only CSV files are allowed.",
    "details": []
  }
}
```

Frontend notes:

- This route returns HTTP `200` for both `success` and `skipped`.
- If you need different UX for a no-op upload, branch on `status`, not status code.

### `POST /api/retrain`

Purpose: kick off the pipeline orchestrator.

Verified validation failure:

```json
{
  "error": {
    "code": "bad_request",
    "message": "Request validation failed.",
    "details": [
      "body.model_selection: Input should be 'ARIMA', 'SARIMA' or 'SARIMAX'"
    ]
  }
}
```

Frontend notes:

- Full retrain success was not smoke-tested in this pass because it launches the full pipeline.
- The request schema uses strict enum-like values, so it is safest for the frontend to submit known literals only.

## Route-Level 404s

Unknown routes also use the shared error envelope:

```json
{
  "error": {
    "code": "not_found",
    "message": "Not Found",
    "details": []
  }
}
```

## Quick Frontend Checklist

- Expect ISO datetime strings everywhere dates appear.
- Treat nullable chart fields as intentional, especially in `/api/forecast-graph/{model_id}`.
- Use the shared `error.code` and `error.message` fields for all handled failures.
- Branch upload UX on `status: "success"` vs `status: "skipped"`.
- Do not assume `/api/dashboard-stats/{model_id}` and `/api/forecast-graph/{model_id}` share the same chart granularity or field names.


## Password Reset (Phase 7)

### URL fragment delivery

Password reset URLs use the URL **fragment** (`#token=...`) rather than a query parameter (`?token=...`). This is a deliberate security choice: fragments are never transmitted to any server, including analytics or third-party resources loaded by the reset page.

The frontend MUST extract the token from `window.location.hash`, not `window.location.search`.

```javascript
const params = new URLSearchParams(window.location.hash.slice(1));
const token = params.get('token');
```

After extracting the token, the frontend SHOULD clear the fragment from the URL via `window.history.replaceState(null, '', window.location.pathname)` to shorten the window during which the token is visible in the address bar.

### Endpoints

- `POST /auth/forgot-password` → `{ email }` → always 200 with generic message (no enumeration).
- `POST /auth/reset-password` → `{ token, new_password }` → 200 on success, 400 on invalid token.
- `POST /admin/users/{user_id}/reset-password` → returns the reset URL in the response body for the admin to share out-of-band.