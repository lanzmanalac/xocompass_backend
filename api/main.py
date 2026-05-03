import json
import logging
import os
import subprocess
import sys
from calendar import monthrange
from sqlalchemy import inspect as sa_inspect

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from pandas.errors import EmptyDataError, ParserError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from jose import JWTError, ExpiredSignatureError
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException
from typing import Optional
from datetime import datetime, timedelta, date, timezone

from math import sqrt
from services.ingestion_service import ingest_csv

load_dotenv()

# ── IMPORT OUR NEW STRICT DATA CONTRACTS ──
from api.schemas import (
    ModelDropdownResponse, DashboardStatsResponse,
    AdvancedMetricsResponse, HistoricalDataResponse,
    ModelDropdownItem, HistoricalDataPoint, ModelParams, ModelStatistics, ModelTests, AdvancedCharts,
    ForecastGraphPoint,
    ForecastGraphResponse,
    StrategicActionsResponse, ModelRenameRequest,
    StrategicAction,
    RetrainRequest,
    RetrainStatusResponse,
    ChartPoint,
    ResidualPoint,
    ModelRenameRequest,
    CorrelationPoint,
    ErrorDetail,
    ErrorResponse,
    HealthResponse,
    DatabaseHealthResponse,
    UploadResponse,
    BusinessAnalyticsResponse, DateCoverage,
    BookingsByYear, BookingsByMonth, HolidayBreakdown,
    LeadTimeBucket, AirlineCount,
    CriticalForecastWeek,
    ForecastOutlookResponse,
    CorrelationHeatmapPoint,
    ValidationPoint,
    DataQualityReport,
    RouteCount, RevenueByYear,
    RevenueByMonth,

)

from services import audit_service
from domain.auth_models import AuditStatus

# ── IMPORT DATABASE ARCHITECTURE ──
from repository.model_repository import SessionLocal
from domain.models import SarimaxModel, DatasetSnapshot, ModelDiagnostic, TrainingDataLog, ForecastCache

# ── IMPORT AUTH ROUTER (Phase 2) ──
# This import has a side effect: importing api.dependencies.auth will
# evaluate core.security at module-load time, which triggers Phase 0's
# _require_env guard against a missing JWT_SECRET_KEY. If the app starts
# successfully, the JWT secret is provably configured. Fail-fast is a
# feature, not a bug. ISO 25010 → Reliability → Fault Tolerance.
from api.routers import auth as auth_router
from api.routers import (  # Phase 4
    admin_users as admin_users_router,
    admin_invitations as admin_invitations_router,
    admin_audit as admin_audit_router,
    admin_system as admin_system_router,
    admin_settings as admin_settings_router,
)
# ── PHASE 5: RBAC DEPENDENCIES ──
from api.dependencies.auth import (
    require_any,
    require_analyst,
    require_admin,
)

from domain.auth_models import User as AuthUser   # alias to avoid clashing with any existing local 'User' identifier

# ── PHASE 6: RATE LIMITER ──
from core.rate_limit import limiter, RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware



logger = logging.getLogger(__name__)

DEFAULT_CORS_ORIGINS = (
    "http://localhost:3000",
    "http://localhost:5173",
    "https://xocompass.vercel.app",
)

def _load_cors_origins() -> list[str]:
    raw_origins = os.getenv("CORS_ALLOWED_ORIGINS")
    if raw_origins is None:
        return list(DEFAULT_CORS_ORIGINS)

    candidate = raw_origins.strip()
    if not candidate:
        return list(DEFAULT_CORS_ORIGINS)

    if candidate.startswith("["):
        try:
            parsed_origins = json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "CORS_ALLOWED_ORIGINS must be a JSON array or a comma-separated list."
            ) from exc

        if not isinstance(parsed_origins, list) or not all(
            isinstance(origin, str) and origin.strip() for origin in parsed_origins
        ):
            raise ValueError(
                "CORS_ALLOWED_ORIGINS must contain one or more non-empty origin strings."
            )

        return [origin.strip() for origin in parsed_origins]

    origins = [origin.strip() for origin in candidate.split(",") if origin.strip()]
    if not origins:
        raise ValueError("CORS_ALLOWED_ORIGINS must contain at least one valid origin.")

    return origins


def _error_code_for_status(status_code: int) -> str:
    return {
        400: "bad_request",
        401: "request_failed",
        404: "not_found",
        429: "rate_limited",
        500: "internal_server_error",
    }.get(status_code, "request_failed")


def _build_error_response(
    status_code: int,
    message: str,
    details: list[str] | None = None,
    code: str | None = None,
) -> JSONResponse:
    payload = ErrorResponse(
        error=ErrorDetail(
            code=code or _error_code_for_status(status_code),
            message=message,
            details=details or [],
        )
    )
    return JSONResponse(status_code=status_code, content=payload.model_dump())


def _extract_http_error_payload(detail: object) -> tuple[str, list[str]]:
    if isinstance(detail, dict):
        message = str(detail.get("message", "Request failed."))
        raw_details = detail.get("details", [])
        if isinstance(raw_details, list):
            details = [str(item) for item in raw_details]
        elif raw_details:
            details = [str(raw_details)]
        else:
            details = []
        return message, details

    if isinstance(detail, list):
        return "Request failed.", [str(item) for item in detail]

    if detail is None:
        return "Request failed.", []

    return str(detail), []


def _normalize_correlation_points(points: list[object] | None) -> list[CorrelationPoint]:
    """Supports both legacy numeric lag arrays and the newer {lag, value} shape."""
    normalized: list[CorrelationPoint] = []

    for lag, point in enumerate(points or []):
        if isinstance(point, dict):
            raw_value = point.get("value")
            if raw_value is None:
                continue
            normalized.append(
                CorrelationPoint(
                    lag=int(point.get("lag", lag)),
                    value=float(raw_value),
                )
            )
            continue

        if isinstance(point, (int, float)):
            normalized.append(CorrelationPoint(lag=lag, value=float(point)))

    return normalized


app = FastAPI(title="XoCompass API", version="10.1")

# ── PHASE 6: RATE LIMITER ──
# `app.state.limiter` is how slowapi retrieves the Limiter instance
# from inside its middleware. The middleware itself attaches the
# X-RateLimit-* response headers and intercepts RateLimitExceeded.
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)

@app.exception_handler(RateLimitExceeded)
async def handle_rate_limit_exceeded(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """
    Surface 429 in the same {"error": {"code", "message", "details"}}
    envelope every other handled error uses. The Retry-After header is
    set by slowapi automatically.

    ISO 25010 → Maintainability → Analyzability: one error format, app-wide.
    """
    del request
    return _build_error_response(
        status_code=429,
        message="Too many requests. Please slow down and try again shortly.",
        code="rate_limited",
    )

# ── MOUNT AUTH ROUTER (Phase 2) ──
# All /auth/* endpoints are unauthenticated by design — they ARE the
# authentication surface. The router itself uses get_current_user only
# on /auth/me. No RBAC retrofit on existing endpoints in this phase;
# Phase 5 handles that.
app.include_router(auth_router.router)

# Phase 4 — admin surface. Each router declares its own /admin/* prefix
# and require_admin guard internally; mounting order is irrelevant for
# behavior but kept stable for OpenAPI tag ordering.
app.include_router(admin_users_router.router)
app.include_router(admin_invitations_router.router)
app.include_router(admin_audit_router.router)
app.include_router(admin_system_router.router)
app.include_router(admin_settings_router.router)

# ════════════════════════════════════════════════════════════════════════════
# 0. CONFIGURATION & DEPENDENCIES
# ════════════════════════════════════════════════════════════════════════════

app.add_middleware(
    CORSMiddleware,
    allow_origins=_load_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(StarletteHTTPException)
async def handle_http_exception(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    del request
    message, details = _extract_http_error_payload(exc.detail)
    return _build_error_response(exc.status_code, message, details)


@app.exception_handler(RequestValidationError)
async def handle_validation_exception(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """
    Convert Pydantic validation errors into the standard error envelope,
    with SPECIAL HANDLING for password-strength errors so the user sees
    the friendly copy instead of "value error, Password must be...".
    
    ISO 25010 → Usability → User Error Protection:
        Generic Pydantic errors are technical debt leaking to the user.
        We translate them into domain-meaningful messages.
    
    ISO 25010 → Maintainability → Analyzability:
        The validation_errors_lookup makes the field-to-message mapping
        explicit. Adding a new field-specific friendly error is one entry.
    """
    del request

    # Field paths whose `value_error` should be surfaced as the PRIMARY
    # message instead of being lumped into details[]. The Pydantic
    # validator's ValueError message becomes the user-facing copy.
    PRIMARY_MESSAGE_FIELDS = {
        "password",          # RegisterRequest.password
        "new_password",      # ResetPasswordRequest.new_password
    }

    primary_message: str | None = None
    details: list[str] = []

    for error in exc.errors():
        location_parts = [str(part) for part in error.get("loc", []) if part != "body"]
        location = ".".join(location_parts)
        message = error.get("msg", "Invalid request.")
        error_type = error.get("type", "")

        # Pydantic v2 prefixes value_error messages with "Value error, ".
        # Strip that so the user sees just our message.
        if message.startswith("Value error, "):
            message = message[len("Value error, "):]

        # If this is a field whose error message we want to PROMOTE to the
        # top-level "message", do so on the first match. Subsequent
        # validation errors still go into details[] for context.
        leaf_field = location_parts[-1] if location_parts else ""
        if (
            primary_message is None
            and leaf_field in PRIMARY_MESSAGE_FIELDS
            and error_type == "value_error"
        ):
            primary_message = message
            continue

        # Standard handling for all other validation errors.
        details.append(f"{location}: {message}" if location else message)

    return _build_error_response(
        status_code=400,
        message=primary_message or "Request validation failed.",
        details=details,
        code="bad_request",
    )
    
# ── PHASE 6: AUTH ERROR HANDLERS ──
# Catch JWT errors that escape the dependency layer (e.g., JWTError
# raised from a service-layer decode that bypassed get_current_user).
# Without these, such errors hit the catch-all 500 handler — wrong.
# A bad token is a 401, not a server error.
#
# ISO 25010 → Reliability → Maturity: failures map to their semantic
# HTTP code, not the catch-all.

@app.exception_handler(ExpiredSignatureError)
async def handle_expired_token(request: Request, exc: ExpiredSignatureError) -> JSONResponse:
    del request, exc
    return _build_error_response(
        status_code=401,
        message="Token has expired.",
        code="token_expired",
    )

@app.exception_handler(JWTError)
async def handle_jwt_error(request: Request, exc: JWTError) -> JSONResponse:
    del request, exc
    return _build_error_response(
        status_code=401,
        message="Could not validate credentials.",
        code="request_failed",
    )

@app.exception_handler(SQLAlchemyError)
async def handle_database_exception(
    request: Request, exc: SQLAlchemyError
) -> JSONResponse:
    logger.error(
        "Database error on %s %s: %s",
        request.method,
        request.url.path,
        exc,
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    return _build_error_response(
        status_code=500,
        message="Database operation failed.",
        code="internal_server_error",
    )


@app.exception_handler(Exception)
async def handle_unexpected_exception(
    request: Request, exc: Exception
) -> JSONResponse:
    logger.error(
        "Unhandled error on %s %s: %s",
        request.method,
        request.url.path,
        exc,
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    return _build_error_response(
        status_code=500,
        message="Internal server error.",
        code="internal_server_error",
    )

def get_db():
    """Dependency to safely open and close database sessions for every request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/")
def home():
    return RedirectResponse(url="/docs")


@app.get("/health", response_model=HealthResponse)
def healthcheck() -> HealthResponse:
    """Confirms that the API process is running."""
    return HealthResponse(status="ok", service="api")


@app.get("/health/db", response_model=DatabaseHealthResponse)
def database_healthcheck(db: Session = Depends(get_db)) -> DatabaseHealthResponse:
    """Confirms that the API can still reach the configured database."""
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        logger.exception("Database health check failed.")
        raise HTTPException(
            status_code=500,
            detail="Database connectivity check failed.",
        ) from exc

    return DatabaseHealthResponse(status="ok", service="api", database="connected")

# ════════════════════════════════════════════════════════════════════════════
# 1. PRESENTATION LAYER ENDPOINTS (Wired to Mock Data)
# ════════════════════════════════════════════════════════════════════════════

@app.get("/api/models", response_model=ModelDropdownResponse)
#def get_model_registry(db: Session = Depends(get_db)):
def get_model_registry(
    db: Session = Depends(get_db),
    _user: AuthUser = Depends(require_any),
):
    """Page 2: Populates the model selection dropdown."""
    models = db.query(SarimaxModel).all()
    if not models:
        raise HTTPException(status_code=404, detail="No trained models found.")

    items = [
        ModelDropdownItem(
            id=m.id, model_name=m.model_name, version=m.pipeline_ver, created_at=m.created_at, aic_score=m.aic_score
        ) for m in models
    ]
    return ModelDropdownResponse(available_models=items)

@app.get("/api/dashboard-stats/{model_id}", response_model=DashboardStatsResponse)
def get_dashboard_stats(
    model_id: int,
    db: Session = Depends(get_db),
    _user: AuthUser = Depends(require_any),
    ):

    """
    Page 1: Returns the fast snapshot metrics for the dashboard.

    ISO 25010 — Reliability → Fault Tolerance:
      Uses hasattr/getattr guards so that pre-migration model rows
      (which lack KPI columns in the DB) return a structured 404
      instead of an unhandled AttributeError that leaks a 500 trace.

    ISO 25010 — Maintainability → Analysability:
      The 'schema_incomplete' detail string tells ops exactly which
      model row is broken and why, without exposing internal tracebacks.
    """
    model = db.query(SarimaxModel).filter(SarimaxModel.id == model_id).first()

    if not model:
        raise HTTPException(status_code=404, detail="Model not found.")

    # GUARD: Detect schema drift or pre-migration model rows.
    # total_records is the sentinel — if it's missing or None, the KPI
    # columns were never written by the orchestrator for this model.
    if not hasattr(model, "total_records") or model.total_records is None:
        logger.warning(
            "Dashboard stats unavailable for model_id=%s: "
            "KPI columns missing or unpopulated. "
            "Run Alembic migrations and retrain.",
            model_id,
        )
        raise HTTPException(
            status_code=404,
            detail={
                "code": "snapshot_missing",
                "message": (
                    f"Dashboard snapshot not available for model {model_id}. "
                    "This model was created before KPI columns were added. "
                    "Please retrain to generate dashboard data."
                ),
                "details": []
            },
        )

    cache_rows = (
        db.query(ForecastCache)
        .filter(ForecastCache.model_id == model_id)
        .order_by(ForecastCache.forecast_date.asc())
        .limit(6)
        .all()
    )

    bookings_forecast = [
        ChartPoint(
            month=row.forecast_date.strftime("%b"),
            actual=0.0,
            predicted=row.predicted or 0.0,
            lowerCI=row.lower_bound or 0.0,
            upperCI=row.upper_bound or 0.0,
        )
        for row in cache_rows
    ]

    raw_yearly = model.yearly_bookings_json
    yearly_bookings = raw_yearly if isinstance(raw_yearly, list) else []

    return DashboardStatsResponse(
        total_records=model.total_records,
        data_quality_pct=model.data_quality_pct or 0.0,
        revenue_total=model.revenue_total or 0.0,
        growth_rate=model.growth_rate or 0.0,
        expected_bookings=model.expected_bookings or 0,
        peak_travel_period=model.peak_travel_period or "",
        bookings_forecast=bookings_forecast,
        yearly_bookings=yearly_bookings,
    )

@app.get("/api/business-analytics", response_model=BusinessAnalyticsResponse)
def get_business_analytics(
    model_id: Optional[int] = None,
    year: Optional[str] = None,          # ── NEW: "overall" | "2013" | "2014" | ...
    db: Session = Depends(get_db),
    _user: AuthUser = Depends(require_any),
):
    """
    Tab 1: Business Analytics — year-aware via ?year= query parameter.

    Behavior:
      - year=None or year="overall" → returns the full-dataset aggregation.
      - year="2023"                 → returns the 2023-scoped slice.
      - year="9999" (invalid)       → returns 400 with sanitized error.
      - model_id provided           → scopes snapshot to that model's training data.
      - model_id omitted            → uses the most recent snapshot.

    ISO 25010:
      Performance Efficiency → Time Behavior:
        Single SELECT on dataset_snapshots. No aggregation on the request path.
        The ?year= filter is a dict lookup on a pre-computed JSON blob — O(1).
      Maintainability → Modifiability:
        _slice_year() is the single point where year-filtering semantics live.
        Adding quarterly view is one new helper — zero schema changes.
      Reliability → Fault Tolerance:
        Invalid year → sanitized 400. Null JSON columns → empty list defaults.
        Never exposes raw Python exceptions to the frontend.
    """

    # ── 1. Resolve the snapshot ───────────────────────────────────────────
    if model_id is not None:
        model = db.query(SarimaxModel).filter(SarimaxModel.id == model_id).first()
        if not model:
            raise HTTPException(
                status_code=404,
                detail={"message": f"Model {model_id} not found.", "details": []},
            )
        if not model.ingestion_batch_id:
            raise HTTPException(
                status_code=404,
                detail={
                    "message": f"Model {model_id} has no linked dataset.",
                    "details": [
                        "This model was trained before dataset tracking was introduced.",
                        "Retrain the model to link it to a dataset snapshot.",
                    ],
                },
            )
        snapshot = db.query(DatasetSnapshot).filter(
            DatasetSnapshot.ingestion_batch_id == model.ingestion_batch_id
        ).first()
    else:
        snapshot = (
            db.query(DatasetSnapshot)
            .order_by(DatasetSnapshot.generated_at.desc())
            .first()
        )

    if not snapshot:
        raise HTTPException(
            status_code=404,
            detail={
                "message": "No dataset snapshot found.",
                "details": ["Upload a CSV via POST /api/upload to generate this data."],
            },
        )

    # ── 2. Validate and normalise the year parameter ──────────────────────
    # Normalise: None and "overall" both mean the full dataset.
    resolved_year = (year or "overall").strip().lower()
    if resolved_year == "overall":
        resolved_year = "overall"
    else:
        available = snapshot.available_years_json or []
        if resolved_year not in available:
            raise HTTPException(
                status_code=400,
                detail={
                    "message": f"Year '{year}' is not available in this dataset.",
                    "details": [f"Available years: {available}"],
                },
            )

    # ── 3. Private slice helper ───────────────────────────────────────────
    # Looks up the correct year slice from a year-keyed JSON dict.
    # Falls back to [] / None gracefully so old NULL columns never crash.
    #
    # ISO 25010 → Reliability → Fault Tolerance:
    #   Every possible bad state (None blob, missing key, wrong type)
    #   returns a safe empty default. The frontend never sees a 500
    #   caused by a missing year key.
    def _slice(blob: dict | list | None, fallback=None):
        if fallback is None:
            fallback = []
        if blob is None:
            return fallback
        # Old snapshots stored a flat list (pre-migration). Return it
        # as-is for the "overall" view so they still work.
        if isinstance(blob, list):
            return blob if resolved_year == "overall" else fallback
        # New snapshots store a year-keyed dict.
        return blob.get(resolved_year, blob.get("overall", fallback))

    # ── 4. Build the year-scoped KPI scalars ──────────────────────────────
    # For scalar KPIs (total_revenue, avg_weekly_bookings, etc.) we use
    # the revenue_by_year dict to get the year-scoped value.
    # For "overall" we use the snapshot's top-level scalar directly.
    rev_by_year_blob = snapshot.revenue_by_year_json  # {"overall": float, "2013": float}

    if resolved_year == "overall" or rev_by_year_blob is None:
        scoped_revenue = snapshot.total_revenue
    else:
        scoped_revenue = rev_by_year_blob.get(resolved_year, snapshot.total_revenue)

    # ── 5. Holiday breakdown — year-scoped ────────────────────────────────
    # Holiday counts are stored on the snapshot as flat integers (global).
    # For the "overall" view we use those directly. For a specific year,
    # we derive them from bookings_by_month filtered to that year — this
    # avoids needing a separate per-year holiday JSON column.
    # The trade-off: holiday counts per year are approximated from the
    # weekly records in TrainingDataLog. Acceptable for a dashboard KPI.
    total_weeks = snapshot.total_weekly_records or 1

    year_records_filtered = []
    if resolved_year == "overall":
        h_weeks     = snapshot.holiday_week_count or 0
        non_h_weeks = snapshot.non_holiday_week_count or 0
    else:
        # Count weekly records for this year from training log
        year_records = (
            db.query(TrainingDataLog)
            .filter(
                TrainingDataLog.ingestion_batch_id == snapshot.ingestion_batch_id,
            )
            .all()
        )
        year_records_filtered = [
            r for r in year_records
            if str(r.record_date.year) == resolved_year
        ]
        h_weeks     = sum(1 for r in year_records_filtered if r.is_holiday)
        non_h_weeks = len(year_records_filtered) - h_weeks
        total_weeks = len(year_records_filtered) or 1

    holiday_pct = round(h_weeks / total_weeks * 100, 1)

    # ── 6. Revenue bar graph — always the full series for the chart ────────
    # The bar graph shows ALL years regardless of the year selector,
    # so the user can see the selected year in context.
    # We convert the revenue_by_year dict to a flat list for the schema.
    if isinstance(rev_by_year_blob, dict):
        revenue_by_year_list = [
            {"year": yr, "revenue": amt}
            for yr, amt in rev_by_year_blob.items()
            if yr != "overall"                        # exclude the summary key
        ]
        revenue_by_year_list.sort(key=lambda x: x["year"])
    else:
        revenue_by_year_list = []

    # ── 7. Data quality ────────────────────────────────────────────────────
    dq_blob  = snapshot.data_quality_json
    dq_slice = _slice(dq_blob, fallback=None)
    data_quality_obj = DataQualityReport(**dq_slice) if dq_slice else None

    # ── Date coverage scoping ─────────────────────────────────────────────
    if resolved_year == "overall":
        coverage_start = snapshot.data_start_date
        coverage_end   = snapshot.data_end_date
        coverage_span  = snapshot.span_weeks or 0
    else:
        year_months = [
            item for item in (snapshot.bookings_by_month_json or [])
            if item.get("month", "").startswith(resolved_year)
        ]
        if year_months:
            first_month = year_months[0]["month"]
            last_month  = year_months[-1]["month"]
            coverage_start = datetime.strptime(first_month, "%Y-%m").replace(
                tzinfo=snapshot.data_start_date.tzinfo
            )
            yr_int, mo_int = int(last_month[:4]), int(last_month[5:])
            last_day = monthrange(yr_int, mo_int)[1]
            coverage_end = datetime(
                yr_int, mo_int, last_day,
                tzinfo=snapshot.data_start_date.tzinfo
            )
            coverage_span = len(year_months)
        else:
            coverage_start = snapshot.data_start_date
            coverage_end   = snapshot.data_end_date
            coverage_span  = 0

    # ── 8. Year-scoped lead time lookup ──────────────────────────────────
    # ISO 25010 — Performance Efficiency > Time Behavior:
    # lead_time_by_year_json is a pre-computed dict keyed by year string.
    # Lookup is O(1). No DB query, no recomputation on the request path.
    lead_time_blob = getattr(snapshot, "lead_time_by_year_json", None) or {}

    if resolved_year == "overall" or not lead_time_blob:
        scoped_avg_lead  = snapshot.avg_lead_time_days
        scoped_lead_dist = [
            LeadTimeBucket(**item)
            for item in (snapshot.lead_time_distribution_json or [])
        ]
    else:
        year_lead = lead_time_blob.get(resolved_year)
        if year_lead:
            scoped_avg_lead  = year_lead.get("avg")
            scoped_lead_dist = [
                LeadTimeBucket(**item)
                for item in (year_lead.get("distribution") or [])
            ]
        else:
            # Year exists in dataset but had no valid travel dates
            scoped_avg_lead  = None
            scoped_lead_dist = []


    return BusinessAnalyticsResponse(
        generated_at=snapshot.generated_at,
        total_transaction_count=snapshot.total_transaction_count or 0,
        total_weekly_records=snapshot.total_weekly_records or 0,
        total_revenue=scoped_revenue,
        avg_weekly_bookings=snapshot.avg_weekly_bookings or 0.0,
        peak_week_date=snapshot.peak_week_date,
        peak_week_bookings=snapshot.peak_week_bookings or 0,
        growth_rate=snapshot.growth_rate or 0.0,
        date_coverage=DateCoverage(
            start_date=coverage_start,
            end_date=coverage_end,
            span_weeks=coverage_span,
        ),

        bookings_by_year=[
            BookingsByYear(**item)
            for item in (snapshot.bookings_by_year_json or [])
            if resolved_year == "overall" or item.get("year") == resolved_year
        ],
        bookings_by_month=[
            BookingsByMonth(**item)
            for item in (snapshot.bookings_by_month_json or [])
            if resolved_year == "overall" or item.get("month", "").startswith(resolved_year)
        ],
        revenue_by_month=[
            RevenueByMonth(**item)
            for item in (snapshot.revenue_by_month_json or [])
            if resolved_year == "overall" or item.get("month", "").startswith(resolved_year)
        ],
        holiday_breakdown=HolidayBreakdown(
            holiday_weeks=h_weeks,
            non_holiday_weeks=non_h_weeks,
            holiday_pct=holiday_pct,
        ),
        avg_lead_time_days=scoped_avg_lead,
        lead_time_distribution=scoped_lead_dist,
        top_airlines=[
            AirlineCount(**item)
            for item in _slice(snapshot.top_airlines_json)
        ],
        top_routes=[
            RouteCount(**item)
            for item in _slice(snapshot.top_routes_json)
        ],
        revenue_by_year=[
            RevenueByYear(**item)
            for item in revenue_by_year_list
        ],
        data_quality=data_quality_obj,
        available_years=snapshot.available_years_json or [],
    )

@app.get("/api/advanced-metrics/{model_id}",
         response_model=AdvancedMetricsResponse)
def get_advanced_metrics(
    model_id: int, 
    db: Session = Depends(get_db),
    _user: AuthUser = Depends(require_any),
    ):
    """Page 2: Full statistical view for the MLOps panel."""

    model = db.query(SarimaxModel).filter(
        SarimaxModel.id == model_id
    ).first()

    diag = db.query(ModelDiagnostic).filter(
        ModelDiagnostic.model_id == model_id
    ).first()

    if not model:
        raise HTTPException(status_code=404,
                            detail=f"Model {model_id} not found.")
    if not diag:
        raise HTTPException(status_code=404,
                            detail=f"Diagnostics for model {model_id} not found.")

    return AdvancedMetricsResponse(
        model_params=ModelParams(
            order=[model.p or 0, model.d or 0, model.q or 0],
            seasonal_order=[
                model.seasonal_p or 0,
                model.seasonal_d or 0,
                model.seasonal_q or 0
            ],
            exogenous_features=model.exog_features_json or []
        ),
        statistics=ModelStatistics(
            rmse=model.rmse or 0.0,
            mae=model.mae or 0.0,
            wmape=model.wmape or 0.0,   # now reads from real column
        ),
        statistical_tests=ModelTests(
            adf_stat=diag.adf_stat or 0.0,
            adf_pvalue=diag.adf_pvalue or 0.0,
            adf_conclusion=diag.adf_conclusion or "Pending",
            ljungbox_stat=diag.ljungbox_stat or 0.0,
            ljungbox_pvalue=diag.ljungbox_pvalue or 0.0,
            ljungbox_conclusion=diag.ljungbox_conclusion or "Pending",
            jarquebera_stat=diag.jarquebera_stat or 0.0,
            jarquebera_pvalue=diag.jarquebera_pvalue or 0.0,
            jarquebera_conclusion=diag.jarquebera_conclusion or "Pending",
        ),
        charts=AdvancedCharts(
            residuals=[ResidualPoint(**r) for r in (diag.residuals_json or [])],
            acf=_normalize_correlation_points(diag.acf_values_json),
            pacf=_normalize_correlation_points(diag.pacf_values_json),
            correlation_heatmap=[
                CorrelationHeatmapPoint(**item)
                for item in (diag.correlation_json or [])
            ],
            validation_graph=[                                        # ── NEW
                ValidationPoint(**item)
                for item in (diag.validation_graph_json or [])
            ],
        )

    )

# ── Horizon constants (change here only, nowhere else) ────────────────────
NEAR_HORIZON  = 2   # Weeks 1-2:  HIGH confidence tier
TOTAL_HORIZON = 12  # Total weeks to surface (2 HIGH + 10 LOWER)

@app.get("/api/forecast-outlook/{model_id}", response_model=ForecastOutlookResponse)
def get_forecast_outlook(
    model_id: int, db: Session = Depends(get_db),
    _user: AuthUser = Depends(require_any),
    ):
    """
    Tab 2: Single endpoint serving KPI cards and Critical Forecast Weeks table.
    One DB query — all aggregation derived in Python from the same in-memory list.

    ISO 25010:
      Performance Efficiency → Time Behavior:
        1 DB query replaces 2. All aggregation is O(n) over ≤16 rows.
      Reliability → Fault Tolerance:
        Null risk_flag defaults to "MEDIUM" with a debug log trace.
        Model existence validated before any cache query.
    """
    model = db.query(SarimaxModel).filter(SarimaxModel.id == model_id).first()
    if not model:
        raise HTTPException(status_code=404, detail=f"Model {model_id} not found.")

    rows = (
        db.query(ForecastCache)
        .filter(ForecastCache.model_id == model_id)
        .order_by(ForecastCache.forecast_date.asc())
        .all()
    )
    if not rows:
        raise HTTPException(
            status_code=404,
            detail="No forecast data found for this model."
        )

    # ── KPI derivations (same list, zero additional DB queries) ───────────
    values = [r.predicted or 0.0 for r in rows]
    forecasted_2w  = int(sum(values[:2]))
    forecasted_10w = int(sum(values[:10]))

    peak_row = max(rows, key=lambda r: r.predicted or 0.0)

    # ── Critical weeks table ──────────────────────────────────────────────
    critical_weeks = []
    for r in rows:
        risk = r.risk_flag if r.risk_flag in ("HIGH", "MEDIUM", "LOW") else "MEDIUM"
        if r.risk_flag is None:
            logger.debug(
                "forecast_cache id=%s has null risk_flag, defaulting to MEDIUM", r.id
            )
        critical_weeks.append(CriticalForecastWeek(
            week_start=r.forecast_date,
            week_end=r.forecast_date + timedelta(days=6),   # weekly cadence: Mon→Sun
            forecasted_volume=int(r.predicted or 0),
            risk_factor=risk,
            confidence_tier=r.confidence_tier if hasattr(r, "confidence_tier") and r.confidence_tier else None,
        ))

    return ForecastOutlookResponse(
        forecasted_bookings_2w=forecasted_2w,
        forecasted_bookings_10w=forecasted_10w,
        highest_forecast_week_date=peak_row.forecast_date,
        highest_forecast_week_value=int(peak_row.predicted or 0),
        critical_weeks=critical_weeks,
    )

@app.get("/api/historical-data", response_model=HistoricalDataResponse)
def get_historical_ledger(db: Session = Depends(get_db), _user: AuthUser = Depends(require_any),):
    records = db.query(TrainingDataLog).order_by(
        TrainingDataLog.record_date.asc()
    ).all()

    if not records:
        raise HTTPException(status_code=404, detail="No historical booking data found.")

    points = []
    for r in records:
        points.append(HistoricalDataPoint(
            date=r.record_date,
            bookings=r.booking_value,
            is_holiday=r.is_holiday,
            weather_indicator=r.weather_indicator
        ))

    return HistoricalDataResponse(data=points)

@app.post("/api/upload", response_model=UploadResponse)
async def upload_pos_data(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: AuthUser = Depends(require_analyst),   # ── PHASE 5 ──
):
    """Page 3: Safely ingests KJS CSV data and prevents duplicate dates.

    Phase 5: now requires ADMIN or ANALYST role. Audit rows carry the
    real actor; the `unauthenticated: true` marker is no longer added
    (the actor is real and `audit_service` only adds the marker when
    actor is None).
    """
    filename = file.filename or "<missing>"

    if not file.filename or not file.filename.lower().endswith(".csv"):
        audit_service.log_action(
            db,
            action_type="DATA_UPLOAD_FAILED",
            status=AuditStatus.FAILED,
            actor=user,                          # ── PHASE 5 ──
            target_resource={"filename": filename},
            request=request,
            metadata={"reason": "non_csv_filename"},
        )
        db.commit()
        raise HTTPException(status_code=400, detail="Only CSV files are allowed.")

    contents = await file.read()
    try:
        result = ingest_csv(contents, db)
    except (ValueError, EmptyDataError, ParserError) as exc:
        db.rollback()
        audit_service.log_action(
            db,
            action_type="DATA_UPLOAD_FAILED",
            status=AuditStatus.FAILED,
            actor=user,                          # ── PHASE 5 ──
            target_resource={"filename": filename},
            request=request,
            metadata={"reason": "parse_or_validation_error", "error": str(exc)[:240]},
        )
        db.commit()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("CSV ingestion failed.")
        audit_service.log_action(
            db,
            action_type="DATA_UPLOAD_FAILED",
            status=AuditStatus.FAILED,
            actor=user,                          # ── PHASE 5 ──
            target_resource={"filename": filename},
            request=request,
            metadata={"reason": "internal_error", "error_type": type(exc).__name__},
        )
        db.commit()
        raise HTTPException(status_code=500, detail="CSV ingestion failed.") from exc

    audit_service.log_action(
        db,
        action_type="DATA_UPLOADED",
        status=AuditStatus.SUCCESS,
        actor=user,                              # ── PHASE 5 ──
        target_resource={"filename": filename},
        request=request,
        metadata={
            "status": result.get("status"),
            "new_records": result.get("new_records"),
        },
    )
    db.commit()
    return UploadResponse(**result)

@app.get("/api/forecast-graph/{model_id}", response_model=ForecastGraphResponse)
def get_forecast_graph(model_id: int, db: Session = Depends(get_db), _user: AuthUser = Depends(require_any),):
    model = db.query(SarimaxModel).filter(SarimaxModel.id == model_id).first()
    if not model:
        raise HTTPException(status_code=404, detail=f"Model {model_id} not found.")

    actuals = db.query(TrainingDataLog).order_by(
        TrainingDataLog.record_date.asc()
    ).all()
    if not actuals:
        raise HTTPException(status_code=404, detail="No historical booking data found.")

    predictions = db.query(ForecastCache).filter(
        ForecastCache.model_id == model_id
    ).order_by(ForecastCache.forecast_date.asc()).all()
    if not predictions:
        raise HTTPException(status_code=404, detail="No forecast data found for this model.")

    # ── Load validation_graph_json from ModelDiagnostic ───────────────────
    # This is the full test-set prediction series already stored by the
    # orchestrator from step6["validation_graph"]. It has actual, forecasted,
    # lower_ci, upper_ci keyed by human-readable date labels like "Jul W2 2023".
    # We build a lookup by parsing those labels back into calendar dates.
    #
    # This is the authoritative source for the test-set overlay — it covers
    # all test weeks, unlike forecast_cache backtest rows which only store ~4.
    #
    # ISO 25010 → Functional Suitability: the chart shows model accuracy
    # against real observed values across the full evaluation window.

    diag = db.query(ModelDiagnostic).filter(
        ModelDiagnostic.model_id == model_id
    ).first()

    validation_lookup: dict = {}
    if diag and diag.validation_graph_json:
        for item in diag.validation_graph_json:
            # Parse "Jul W2 2023" → approximate calendar date for lookup
            # Strategy: map to the Monday of the indicated week-of-month
            try:
                label = item["date_label"]           # e.g. "Jul W2 2023"
                parts = label.split()                 # ["Jul", "W2", "2023"]
                month_abbr = parts[0]
                week_num   = int(parts[1][1])         # W2 → 2
                year       = int(parts[2])

                import calendar
                month_num = list(calendar.month_abbr).index(month_abbr)

                # Find the date of week_num-th Monday in that month/year
                first_day = datetime(year, month_num, 1)
                # Day of week: Monday=0 ... Sunday=6
                first_monday_offset = (7 - first_day.weekday()) % 7
                # If first_day is already Monday, offset=0
                if first_day.weekday() == 0:
                    first_monday_offset = 0
                first_monday = first_day + timedelta(days=first_monday_offset)
                target_monday = first_monday + timedelta(weeks=week_num - 1)

                validation_lookup[target_monday.date()] = item
            except Exception as e:
                logger.debug("Could not parse validation_graph label '%s': %s",
                             item.get("date_label"), e)

    # ── Forward forecast rows only from ForecastCache ─────────────────────
    forward_predictions = [p for p in predictions if (p.periods_ahead or 0) > 0]

    actual_dates = {r.record_date.date() for r in actuals}

    points = []

    # ── Historical actuals — merge validation predictions where available ──
    for r in actuals:
        date_key = r.record_date.date()
        vg = validation_lookup.get(date_key)

        points.append(ForecastGraphPoint(
            date=r.record_date,
            actual=r.booking_value,
            predicted=round(float(vg["forecasted"]), 2) if vg else None,
            lower_bound=round(float(vg["lower_ci"]), 2) if vg else None,
            upper_bound=round(float(vg["upper_ci"]), 2) if vg else None,
            confidence_tier="BACKTEST" if vg else None,
        ))

    # ── Forward forecast rows ─────────────────────────────────────────────
    for p in forward_predictions:
        if p.forecast_date.date() in actual_dates:
            logger.debug(
                "Skipping forward forecast point %s — date already in actuals.",
                p.forecast_date.date()
            )
            continue
        points.append(ForecastGraphPoint(
            date=p.forecast_date,
            actual=None,
            predicted=p.predicted,
            lower_bound=p.lower_bound,
            upper_bound=p.upper_bound,
            confidence_tier=p.confidence_tier,
        ))

    return ForecastGraphResponse(data=points)
    

@app.get("/api/strategic-actions/{model_id}", 
         response_model=StrategicActionsResponse)
def get_strategic_actions(model_id: int, db: Session = Depends(get_db), _user: AuthUser = Depends(require_any),):
    """
    Page 1: Prescriptive analytics.
    Derives actionable recommendations from forecast_cache data.
    Rule-based engine — no ML required.
    """
    model = db.query(SarimaxModel).filter(SarimaxModel.id == model_id).first()
    if not model:
        raise HTTPException(status_code=404, detail=f"Model {model_id} not found.")

    predictions = db.query(ForecastCache).filter(
        ForecastCache.model_id == model_id
    ).order_by(ForecastCache.forecast_date.asc()).all()

    if not predictions:
        raise HTTPException(status_code=404, 
                          detail="No forecast data found for this model.")

    actions = []
    values = [p.predicted for p in predictions]
    peak_value = max(values)
    trough_value = min(values)
    avg_value = sum(values) / len(values)

    # Rule 1: Peak season detected
    if peak_value > avg_value * 1.3:
        peak_week = predictions[values.index(peak_value)]
        actions.append(StrategicAction(
            priority="HIGH",
            category="Staffing",
            action="Increase booking staff by 30% during peak demand window.",
            trigger=f"Forecast peaks at {peak_value:.0f} bookings — "
                   f"30% above average near "
                   f"{peak_week.forecast_date.strftime('%B %Y')}."
        ))
        actions.append(StrategicAction(
            priority="HIGH",
            category="Pricing",
            action="Apply premium pricing tier during peak weeks "
                   "to maximize revenue per booking.",
            trigger=f"Demand surge detected above "
                   f"{avg_value * 1.3:.0f} bookings/week threshold."
        ))

    # Rule 2: Low season detected
    if trough_value < avg_value * 0.7:
        actions.append(StrategicAction(
            priority="MEDIUM",
            category="Marketing",
            action="Launch promotional packages 6 weeks before "
                   "forecasted low-demand period.",
            trigger=f"Demand trough of {trough_value:.0f} bookings "
                   f"forecasted — 30% below average."
        ))

    # Rule 3: Growth trend
    if values[-1] > values[0]:
        actions.append(StrategicAction(
            priority="LOW",
            category="Pricing",
            action="Review base pricing quarterly — "
                   "upward booking trend supports gradual rate increases.",
            trigger="16-week forecast shows positive demand trajectory."
        ))

    period = (f"{predictions[0].forecast_date.strftime('%B %Y')} – "
              f"{predictions[-1].forecast_date.strftime('%B %Y')}")

    return StrategicActionsResponse(
        actions=actions,
        generated_for_period=period
    )

@app.post("/api/retrain", response_model=RetrainStatusResponse)
def trigger_retrain(
    body: RetrainRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: AuthUser = Depends(require_analyst),
):
    """
    Phase 3: every retrain — successful or failed — writes an audit row.
    The action_type is FORECAST_RUN on success, FORECAST_FAILED otherwise.
    The audit row's metadata captures the request payload (which exog
    factors, which model selection) so a future "why was this model
    trained?" investigation has the inputs preserved.

    Phase 5: requires Admin or Analyst. Every retrain — successful or failed —
    writes an audit row attributing the actor.
    """
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_json_string = body.model_dump_json()
    started_at = datetime.now(timezone.utc)

    try:
        result = subprocess.run(
            [sys.executable, "-m", "services.pipeline.orchestrator"],
            input=config_json_string,
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=1500,
        )
        print("=== ORCHESTRATOR STDOUT ===", flush=True)
        print(result.stdout, flush=True)
        print("=== ORCHESTRATOR STDERR ===", flush=True)
        print(result.stderr, flush=True)

        if result.returncode != 0:
            logger.error("Retraining pipeline failed: %s", result.stderr[-500:])
            audit_service.log_action(
                db,
                action_type="FORECAST_FAILED",
                status=AuditStatus.FAILED,
                actor=None,
                request=request,
                metadata={
                    "model_selection": body.model_selection,
                    "time_period": body.time_period,
                    "external_factors": body.external_factors,
                    "returncode": result.returncode,
                    "stderr_tail": (result.stderr or "")[-500:],
                    "duration_seconds": (datetime.now(timezone.utc) - started_at).total_seconds(),
                },
            )
            db.commit()
            raise HTTPException(status_code=500, detail="Model retraining failed.")

    except subprocess.TimeoutExpired as exc:
        logger.exception("Retraining pipeline timed out.")
        audit_service.log_action(
            db,
            action_type="FORECAST_FAILED",
            status=AuditStatus.FAILED,
            actor=None,
            request=request,
            metadata={
                "model_selection": body.model_selection,
                "reason": "timeout",
                "timeout_seconds": 1500,
            },
        )
        db.commit()
        raise HTTPException(
            status_code=500,
            detail="Model retraining timed out after 25 minutes.",
        ) from exc
    except OSError as exc:
        logger.exception("Retraining pipeline could not be started.")
        audit_service.log_action(
            db,
            action_type="FORECAST_FAILED",
            status=AuditStatus.FAILED,
            actor=None,
            request=request,
            metadata={
                "model_selection": body.model_selection,
                "reason": "subprocess_failed_to_start",
                "error_type": type(exc).__name__,
            },
        )
        db.commit()
        raise HTTPException(
            status_code=500,
            detail="Model retraining could not be started.",
        ) from exc

    db.commit()
    db.expire_all()

    new_model = db.query(SarimaxModel).filter(
        SarimaxModel.is_active == True).first()

    if not new_model:
        audit_service.log_action(
            db,
            action_type="FORECAST_FAILED",
            status=AuditStatus.FAILED,
            actor=None,
            request=request,
            metadata={
                "reason": "post_pipeline_no_active_model",
                "model_selection": body.model_selection,
            },
        )
        db.commit()
        raise HTTPException(
            status_code=500,
            detail="Model retraining finished but no active model was found.",
        )

    new_records_used = db.query(TrainingDataLog).count()

    # ── Success audit ──────────────────────────────────────────────────────
    audit_service.log_action(
        db,
        action_type="FORECAST_RUN",
        status=AuditStatus.SUCCESS,
        actor=None,
        target_resource={"model_id": new_model.id, "model_name": new_model.model_name},
        request=request,
        metadata={
            "model_selection": body.model_selection,
            "time_period": body.time_period,
            "external_factors": body.external_factors,
            "new_records_used": new_records_used,
            "aic_score": new_model.aic_score,
            "duration_seconds": (datetime.now(timezone.utc) - started_at).total_seconds(),
        },
    )
    db.commit()

    return RetrainStatusResponse(
        status="success",
        message=f"Model retrained successfully. New Model ID: {new_model.id}",
        new_records_used=new_records_used,
    )

# ── 1. RENAME ENDPOINT ──
@app.patch("/api/models/{model_id}/rename")
def rename_model(
    model_id: int,
    body: ModelRenameRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: AuthUser = Depends(require_analyst),
):
    """
    Page 2: Renames a specific model in the registry.

    Phase 3: every successful rename writes a MODEL_RENAMED audit row
    with both the old and the new name. The audit log preserves rename
    history that the SarimaxModel row itself does not — once you've
    renamed Model #42 from "v10.1-experimental" to "production",
    the column shows the new name and history is lost without the audit.

    Phase 5 will add Depends(require_analyst); today actor=None.
    """
    model = db.query(SarimaxModel).filter(SarimaxModel.id == model_id).first()
    if not model:
        raise HTTPException(status_code=404, detail="Model not found.")

    old_name = model.model_name
    new_name = body.new_model_name

    if old_name == new_name:
        # No-op rename — audit it as SUCCESS for completeness, but with
        # a `noop: true` marker so dashboards can filter it out.
        audit_service.log_action(
            db,
            action_type="MODEL_RENAMED",
            status=AuditStatus.SUCCESS,
            actor=None,
            target_resource={"model_id": model.id},
            request=request,
            metadata={"old_name": old_name, "new_name": new_name, "noop": True},
        )
        db.commit()
        return {
            "status": "success",
            "message": f"Model '{old_name}' name unchanged.",
        }

    model.model_name = new_name

    audit_service.log_action(
        db,
        action_type="MODEL_RENAMED",
        status=AuditStatus.SUCCESS,
        actor=None,
        target_resource={"model_id": model.id, "model_name": new_name},
        request=request,
        metadata={"old_name": old_name, "new_name": new_name},
    )
    db.commit()

    return {
        "status": "success",
        "message": f"Model '{old_name}' renamed to '{new_name}'",
    }


@app.delete("/api/models/{model_id}")
def delete_model(
    model_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: AuthUser = Depends(require_admin),
):
    """
    Phase 3: every successful deletion writes a MODEL_DELETED audit row
    with the model_name and key metadata captured BEFORE the delete —
    forensic preservation of "what was here." Cascade also wipes
    ModelDiagnostic and ForecastCache rows; their counts go in metadata.

    Phase 5 will add Depends(require_admin); today actor=None.
    """
    model = db.query(SarimaxModel).filter(SarimaxModel.id == model_id).first()
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")

    # Snapshot identifying details BEFORE the delete. Once db.delete(model)
    # commits, the ORM object is detached and these attribute reads can
    # raise. Capture eagerly.
    snapshot = {
        "model_id": model.id,
        "model_name": model.model_name,
        "pipeline_ver": model.pipeline_ver,
        "aic_score": model.aic_score,
        "is_active": bool(model.is_active),
        "model_path": model.model_path,
    }

    # File deletion is best-effort — failure here doesn't block DB deletion.
    file_removed = False
    if model.model_path and os.path.exists(model.model_path):
        try:
            os.remove(model.model_path)
            file_removed = True
        except Exception as e:
            print(f"Warning: Could not delete model file {model.model_path}: {e}")

    db.delete(model)

    audit_service.log_action(
        db,
        action_type="MODEL_DELETED",
        status=AuditStatus.SUCCESS,
        actor=None,
        target_resource={"model_id": snapshot["model_id"], "model_name": snapshot["model_name"]},
        request=request,
        metadata={
            **snapshot,
            "file_removed": file_removed,
        },
    )
    db.commit()

    return {"message": f"Model {model_id} and all its data were successfully obliterated."}
    

# ─────────────────────────────────────────────────────────────────────────────
# DEBUG ENDPOINTS — Phase 5
#
# Gated by BOTH:
#   1. ENVIRONMENT != "production" — refuses to even register the route
#      in production. The endpoint literally does not exist on prod
#      revisions; a probe gets a 404, not a 403, eliminating the
#      "this resource exists but you can't reach it" leak.
#   2. require_admin — even in development, only Admins see the truth
#      of what DB we're talking to.
#
# ISO 25010 → Security → Confidentiality at the deployment boundary.
# ─────────────────────────────────────────────────────────────────────────────

_ENVIRONMENT = (os.getenv("ENVIRONMENT") or "development").strip().lower()
_DEBUG_ROUTES_ENABLED = _ENVIRONMENT != "production"


if _DEBUG_ROUTES_ENABLED:
    @app.get("/_debug/db-truth")
    def debug_db_truth(
        db: Session = Depends(get_db),
        _admin: AuthUser = Depends(require_admin),
    ):
        """Verifies which DB the app is actually reading from.
        Available only when ENVIRONMENT != 'production' AND caller is Admin."""
        result = db.execute(text("""
            SELECT
                current_database() AS db_name,
                inet_server_addr() AS server_ip,
                (SELECT COUNT(*) FROM sarimax_models) AS model_count
        """)).fetchone()

        return {
            "connected_to_db": result[0],
            "server_internal_ip": str(result[1]),
            "models_found_in_this_db": result[2],
            "is_using_neon": "neon.tech" in str(db.get_bind().url),
            "environment": _ENVIRONMENT,
        }
else:
    # Production: the /_debug/db-truth path is intentionally not registered.
    # A probe receives 404, indistinguishable from a typo.
    logger.info(
        "_debug routes are DISABLED for environment=%s. /_debug/* returns 404.",
        _ENVIRONMENT,
    )