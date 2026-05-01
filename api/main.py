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
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException
from typing import Optional
from datetime import datetime

from services.ingestion_service import ingest_csv

# ── IMPORT OUR NEW STRICT DATA CONTRACTS ──
from api.schemas import (
    ModelDropdownResponse, DashboardStatsResponse,
    AdvancedMetricsResponse, HistoricalDataResponse,
    ModelDropdownItem, HistoricalDataPoint, ModelParams, ModelStatistics, ModelTests, AdvancedCharts,
    ForecastGraphPoint,
    ForecastGraphResponse,
    StrategicActionsResponse,
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

# ── IMPORT DATABASE ARCHITECTURE ──
from repository.model_repository import SessionLocal
from domain.models import SarimaxModel, DatasetSnapshot, ModelDiagnostic, TrainingDataLog, ForecastCache

load_dotenv()

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
        404: "not_found",
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
    del request

    details = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", []))
        message = error.get("msg", "Invalid request.")
        details.append(f"{location}: {message}" if location else message)

    return _build_error_response(
        status_code=400,
        message="Request validation failed.",
        details=details,
        code="bad_request",
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
def get_model_registry(db: Session = Depends(get_db)):
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
def get_dashboard_stats(model_id: int, db: Session = Depends(get_db)):
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
        ],

        holiday_breakdown=HolidayBreakdown(
            holiday_weeks=h_weeks,
            non_holiday_weeks=non_h_weeks,
            holiday_pct=holiday_pct,
        ),
        avg_lead_time_days=snapshot.avg_lead_time_days,
        lead_time_distribution=[
            LeadTimeBucket(**item)
            for item in _slice(snapshot.lead_time_distribution_json)
        ],
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
def get_advanced_metrics(model_id: int, db: Session = Depends(get_db)):
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

@app.get("/api/forecast-outlook/{model_id}", response_model=ForecastOutlookResponse)
def get_forecast_outlook(model_id: int, db: Session = Depends(get_db)):
    """
    Tab 2: KPI cards + 12-week critical forecast weeks table.
    Only queries FORWARD rows (periods_ahead > 0) — excludes BACKTEST rows.

    ISO 25010 Reliability → Fault Tolerance:
      risk_flag null defaults to MEDIUM with debug log. Never 500s.
    """
    model = db.query(SarimaxModel).filter(SarimaxModel.id == model_id).first()
    if not model:
        raise HTTPException(status_code=404, detail=f"Model {model_id} not found.")

    # Explicitly exclude BACKTEST rows from KPI and table calculations
    rows = (
        db.query(ForecastCache)
        .filter(
            ForecastCache.model_id == model_id,
            ForecastCache.confidence_tier != "BACKTEST",
        )
        .order_by(ForecastCache.forecast_date.asc())
        .all()
    )
    if not rows:
        raise HTTPException(status_code=404, detail="No forecast data found for this model.")

    values = [r.predicted or 0.0 for r in rows]
    forecasted_2w = int(sum(values[:2]))
    peak_row = max(rows, key=lambda r: r.predicted or 0.0)

    # Confidence tier thresholds — tune these to your CI width interpretation.
    # Currently: weeks 1–2 = HIGH confidence, weeks 3+ = LOWER confidence.
    # This mirrors the frontier your SARIMAX CI bands widen after week 2.
    HIGH_CONFIDENCE_HORIZON = 2

    critical_weeks = []
    for idx, r in enumerate(rows):
        # ── Risk flag with defensive fallback ────────────────────────────
        risk = r.risk_flag if r.risk_flag in ("HIGH", "MEDIUM", "LOW") else "MEDIUM"
        if r.risk_flag is None:
            logger.debug(
                "forecast_cache id=%s has null risk_flag, defaulting to MEDIUM", r.id
            )

        # ── Confidence tier: derived from forecast horizon position ──────
        # Weeks 1–2 (idx 0,1): CI bands are narrow → HIGH confidence.
        # Weeks 3+  (idx 2+):  CI bands widen with horizon → LOWER confidence.
        # This is a direct reflection of SARIMAX's increasing uncertainty
        # over multi-step forecasts — defensible at thesis defense.
        confidence_tier = "HIGH" if idx < HIGH_CONFIDENCE_HORIZON else "LOWER"

        # ── week_end: always Saturday/Sunday of the same ISO week ────────
        # forecast_date is always a Monday (weekly freq anchor).
        # Adding timedelta(days=6) gives the Sunday — the natural week boundary
        # for a Mon–Sun travel industry week.
        week_end = r.forecast_date + timedelta(days=6)

        critical_weeks.append(CriticalForecastWeek(
            week_start=r.forecast_date,
            week_end=week_end,
            forecasted_volume=int(r.predicted or 0),
            risk_factor=risk,
            confidence_tier=confidence_tier,
        ))

    return ForecastOutlookResponse(
        forecasted_bookings_2w=forecasted_2w,
        highest_forecast_week_date=peak_row.forecast_date,
        highest_forecast_week_value=int(peak_row.predicted or 0),
        critical_weeks=critical_weeks,
    )

@app.get("/api/historical-data", response_model=HistoricalDataResponse)
def get_historical_ledger(db: Session = Depends(get_db)):
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
async def upload_pos_data(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Page 3: Safely ingests KJS CSV data and prevents duplicate dates."""
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only CSV files are allowed.")

    contents = await file.read()
    try:
        result = ingest_csv(contents, db)
        return UploadResponse(**result)
    except (ValueError, EmptyDataError, ParserError) as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("CSV ingestion failed.")
        raise HTTPException(status_code=500, detail="CSV ingestion failed.") from exc

@app.get("/api/forecast-graph/{model_id}", response_model=ForecastGraphResponse)
def get_forecast_graph(model_id: int, db: Session = Depends(get_db)):
    """
    Tab 2: Forecast graph — windowed view only.
    Returns exactly 16 points:
      - 4 BACKTEST rows: model predictions on trailing test-set weeks,
        with actual booking values stored directly on the cache row.
      - 2 HIGH rows: weather-backed forward forecast.
      - 10 LOWER rows: climatological proxy forward forecast.
    """
    model = db.query(SarimaxModel).filter(SarimaxModel.id == model_id).first()
    if not model:
        raise HTTPException(status_code=404, detail=f"Model {model_id} not found.")

    cache_rows = (
        db.query(ForecastCache)
        .filter(ForecastCache.model_id == model_id)
        .order_by(ForecastCache.forecast_date.asc())
        .all()
    )
    if not cache_rows:
        raise HTTPException(status_code=404, detail="No forecast data found for this model.")

    # ── Separate BACKTEST from forward rows ───────────────────────────────
    backtest_rows = [r for r in cache_rows if r.confidence_tier == "BACKTEST"]
    forward_rows  = [r for r in cache_rows if r.confidence_tier != "BACKTEST"]

    points = []

    # ── BACKTEST rows — actual_bookings stored directly on the cache row ──
    # No cross-table TrainingDataLog lookup needed anymore.
    # actual_bookings was written by orchestrator.py at training time.
    for r in backtest_rows:
        points.append(ForecastGraphPoint(
            date=r.forecast_date,
            actual=r.actual_bookings,
            predicted=r.predicted,
            lower_bound=r.lower_bound,
            upper_bound=r.upper_bound,
            confidence_tier=r.confidence_tier,
        ))

    # ── Forward rows (HIGH + LOWER) — no actuals yet ──────────────────────
    for r in forward_rows:
        points.append(ForecastGraphPoint(
            date=r.forecast_date,
            actual=None,
            predicted=r.predicted,
            lower_bound=r.lower_bound,
            upper_bound=r.upper_bound,
            confidence_tier=r.confidence_tier,
        ))

    # ── Sort chronologically ──────────────────────────────────────────────
    points.sort(key=lambda p: p.date)

    return ForecastGraphResponse(data=points)

@app.get("/api/strategic-actions/{model_id}", 
         response_model=StrategicActionsResponse)
def get_strategic_actions(model_id: int, db: Session = Depends(get_db)):
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
    request: RetrainRequest,
    db: Session = Depends(get_db)
):
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_json_string = request.model_dump_json()

    try:
        result = subprocess.run(
            [sys.executable, "-m", "services.pipeline.orchestrator"],
            input=config_json_string,
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=1500            
        )
        # Force the hidden logs to print to Google Cloud Run
        print("=== ORCHESTRATOR STDOUT ===", flush=True)
        print(result.stdout, flush=True)
        print("=== ORCHESTRATOR STDERR ===", flush=True)
        print(result.stderr, flush=True)

        if result.returncode != 0:
            logger.error("Retraining pipeline failed: %s", result.stderr[-500:])
            raise HTTPException(
                status_code=500,
                detail="Model retraining failed."
            )
    except subprocess.TimeoutExpired as exc:
        logger.exception("Retraining pipeline timed out.")
        raise HTTPException(
            status_code=500,
            detail="Model retraining timed out after 25 minutes.",
        ) from exc
    except OSError as exc:
        logger.exception("Retraining pipeline could not be started.")
        raise HTTPException(
            status_code=500,
            detail="Model retraining could not be started.",
        ) from exc

    # Force the DB connection to wake up and look at the present!
    db.commit()
    db.expire_all()

    new_model = db.query(SarimaxModel).filter(
        SarimaxModel.is_active == True).first()

    if not new_model:
        raise HTTPException(
            status_code=500,
            detail="Model retraining finished but no active model was found.",
        )

    return RetrainStatusResponse(
        status="success",
        message=f"Model retrained successfully. New Model ID: {new_model.id}",
        new_records_used=db.query(TrainingDataLog).count()
    )

from api.schemas import ModelRenameRequest 

# ── 1. RENAME ENDPOINT ──
@app.patch("/api/models/{model_id}/rename")
def rename_model(model_id: int, request: ModelRenameRequest, db: Session = Depends(get_db)):
    """Page 2: Renames a specific model in the registry."""
    model = db.query(SarimaxModel).filter(SarimaxModel.id == model_id).first()
    
    if not model:
        raise HTTPException(status_code=404, detail="Model not found.")
        
    old_name = model.model_name
    model.model_name = request.new_model_name
    db.commit()
    
    return {"status": "success", "message": f"Model '{old_name}' renamed to '{request.new_model_name}'"}


# ── 2. DELETE ENDPOINT (Safe Deletion) ──
import os

@app.delete("/api/models/{model_id}")
def delete_model(model_id: int, db: Session = Depends(get_db)):
    # 1. Find the model
    model = db.query(SarimaxModel).filter(SarimaxModel.id == model_id).first()
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")

    # 2. Physically delete the .joblib file from your Mac/Server so it doesn't clog storage
    if model.model_path and os.path.exists(model.model_path):
        try:
            os.remove(model.model_path)
        except Exception as e:
            print(f"Warning: Could not delete model file {model.model_path}: {e}")

    # 3. Nuke the model from the database
    # (Because of cascade="all, delete-orphan" in models.py, this single line 
    # automatically destroys the associated ModelDiagnostic and ForecastCache records too!)
    db.delete(model)
    db.commit()

    return {"message": f"Model {model_id} and all its data were successfully obliterated."}
from sqlalchemy import text

@app.get("/_debug/db-truth")
def debug_db_truth(db: Session = Depends(get_db)):
    """Verifies which DB the app is actually reading from."""
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
        "is_using_neon": "neon.tech" in str(db.get_bind().url)
    }

@app.get("/_debug/migration-check")
def check_migration_state(db: Session = Depends(get_db)):
    """
    Verifies that all KPI columns from migration c1d2e3f4g5h6 exist
    in the live sarimax_models table.

    ISO 25010 — Reliability → Maturity:
      Ops-level endpoint that confirms schema state without requiring
      direct DB access. Should be called after every Cloud Run deploy.
    """
    required_columns = [
        "total_records", "data_quality_pct", "revenue_total",
        "growth_rate", "expected_bookings", "peak_travel_period",
        "yearly_bookings_json",
    ]
    inspector = sa_inspect(db.bind)
    existing = {
        col["name"]
        for col in inspector.get_columns("sarimax_models")
    }
    missing = [c for c in required_columns if c not in existing]

    return {
        "migration_c1d2e3f4g5h6_applied": len(missing) == 0,
        "missing_columns": missing,
        "existing_kpi_columns": [c for c in required_columns if c in existing],
    }