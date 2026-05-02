from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Literal
from datetime import datetime, timedelta

# ════════════════════════════════════════════════════════════════════════════
# SHARED UTILITY SCHEMAS
# ════════════════════════════════════════════════════════════════════════════

class ModelDropdownItem(BaseModel):
    id: int
    model_name: str
    version: str
    created_at: datetime                         
    aic_score: Optional[float] = None
    notes: Optional[str] = None

class ModelDropdownResponse(BaseModel):
    available_models: List[ModelDropdownItem]

class ErrorDetail(BaseModel):
    code: str
    message: str
    details: List[str] = Field(default_factory=list)

class ErrorResponse(BaseModel):
    error: ErrorDetail

class HealthResponse(BaseModel):
    status: str
    service: str

class DatabaseHealthResponse(HealthResponse):
    database: str

class UploadResponse(BaseModel):
    status: str
    message: str
    new_records: int

# ════════════════════════════════════════════════════════════════════════════
# TAB 1: BUSINESS ANALYTICS
# Endpoint: GET /api/business-analytics?year=<year|overall>
# Source:   dataset_snapshots (dataset-scoped, no model_id)
# ════════════════════════════════════════════════════════════════════════════

class DateCoverage(BaseModel):
    start_date: datetime
    end_date: datetime
    span_weeks: int

class BookingsByYear(BaseModel):
    year: str
    bookings: int

class BookingsByMonth(BaseModel):
    month: str      # format: "2023-01"
    bookings: int

class RevenueByMonth(BaseModel):
    """
    Monthly revenue aggregate.
    Parallel structure to BookingsByMonth — same month key format ("YYYY-MM")
    so the frontend can zip both arrays on the same x-axis without any
    client-side date parsing.

    ISO 25010 — Maintainability → Reusability:
        Defined as a standalone class so future endpoints (e.g. revenue
        forecasting) can reference this schema without duplication.
    """
    month: str      # format: "2023-01"
    revenue: float


class HolidayBreakdown(BaseModel):
    holiday_weeks: int
    non_holiday_weeks: int
    holiday_pct: float

class LeadTimeBucket(BaseModel):
    bucket: str
    count: int

class AirlineCount(BaseModel):
    airline_code: str
    count: int
    pct: float

# ── NEW ──────────────────────────────────────────────────────────────────────

class RouteCount(BaseModel):
    route: str
    count: int
    pct: float

class RevenueByYear(BaseModel):
    # Flat list for the bar graph: [{"year": "2013", "revenue": 3200000.0}, ...]
    year: str
    revenue: float

class DataQualityReport(BaseModel):
    total_rows: int
    duplicate_rows: int
    missing_airline: int
    missing_route: int
    missing_travel_date: int
    invalid_travel_date: int
    missing_revenue: int
    quality_score_pct: float

# ─────────────────────────────────────────────────────────────────────────────

class BusinessAnalyticsResponse(BaseModel):
    generated_at: datetime
    total_transaction_count: int
    total_weekly_records: int
    total_revenue: Optional[float]
    avg_weekly_bookings: float
    peak_week_date: datetime
    peak_week_bookings: int
    growth_rate: float
    date_coverage: DateCoverage
    bookings_by_year: List[BookingsByYear]
    bookings_by_month: List[BookingsByMonth]
    revenue_by_month: List[RevenueByMonth] = []   # ← ADD — default [] for backwards compat
    holiday_breakdown: HolidayBreakdown

    avg_lead_time_days: Optional[float] = None
    lead_time_distribution: List[LeadTimeBucket] = []
    top_airlines: List[AirlineCount] = []

    # ── NEW fields ────────────────────────────────────────────────────────
    top_routes: List[RouteCount] = []
    revenue_by_year: List[RevenueByYear] = []
    data_quality: Optional[DataQualityReport] = None
    available_years: List[str] = []


# ════════════════════════════════════════════════════════════════════════════
# PAGE 1: SIMPLIFIED METRICS (Executive Dashboard)
# ════════════════════════════════════════════════════════════════════════════
class ChartPoint(BaseModel):
    month: str
    actual: float
    predicted: float
    lowerCI: float
    upperCI: float

class ForecastGraphPoint(BaseModel):
    date: datetime
    actual: Optional[float] = None
    predicted: Optional[float] = None
    lower_bound: Optional[float] = None
    upper_bound: Optional[float] = None
    confidence_tier: Optional[str] = None   # ← must be here

class ForecastGraphResponse(BaseModel):
    data: List[ForecastGraphPoint]

class YearlyBookingPoint(BaseModel):
    year: str
    bookings: int

class DashboardStatsResponse(BaseModel):
    total_records: int
    data_quality_pct: float
    revenue_total: float
    growth_rate: float
    expected_bookings: int
    peak_travel_period: str
    bookings_forecast: List[ChartPoint] = Field(
        default_factory=list,
        description=(
            "Filler"
        ),
    )
    yearly_bookings: List[YearlyBookingPoint] = []

class StrategicAction(BaseModel):
    priority: str        # "HIGH" | "MEDIUM" | "LOW"
    category: str        # "Pricing" | "Staffing" | "Marketing"
    action: str          # human-readable recommendation
    trigger: str         # what forecast condition triggered this

class StrategicActionsResponse(BaseModel):
    actions: List[StrategicAction]
    generated_for_period: str

# ════════════════════════════════════════════════════════════════════════════
# PAGE 2: ADVANCED METRICS (MLOps View)
# ════════════════════════════════════════════════════════════════════════════
# --- Coordinate Shapes for Charts ---
class ResidualPoint(BaseModel):
    fitted: float
    residual: float


class CorrelationPoint(BaseModel):
    lag: int
    value: float

class ModelParams(BaseModel):
    order: List[int]          # [p, d, q]
    seasonal_order: List[int] # [P, D, Q]
    exogenous_features: List[str]

class ModelStatistics(BaseModel):
    rmse: float
    mae: float
    wmape: float
    
class ModelTests(BaseModel):
    adf_stat: float
    adf_pvalue: float
    adf_conclusion: str         # "Series is stationary" | "Series is non-stationary"
    ljungbox_stat: float        # ADD THIS — you have it in DB
    ljungbox_pvalue: float
    ljungbox_conclusion: str    # ADD THIS — for the diagnostics table
    jarquebera_stat: float      # ADD THIS
    jarquebera_pvalue: float
    jarquebera_conclusion: str  # ADD THIS

# ════════════════════════════════════════════════════════════════════════════
# PAGE 3: TIME SERIES LAB (Data & Retraining)
# ════════════════════════════════════════════════════════════════════════════

class HistoricalDataPoint(BaseModel):
    date: datetime
    bookings: float
    is_holiday: bool
    weather_indicator: Optional[float] = None

class HistoricalDataResponse(BaseModel):
    data: List[HistoricalDataPoint]

class RetrainRequest(BaseModel):
    time_period: Literal["7 Days", "14 Days", "21 Days", "30 Days", "90 Days", "Whole Data Set (2013-Present)"] = "30 Days"
    target_variable: Literal["Booking Date"] = "Booking Date"
    external_factors: List[str] = ["Typhoon", "Rainfall Index", "Temperature", "Wind Speed", "Holiday"]
    model_selection: Literal["ARIMA", "SARIMA", "SARIMAX"] = "SARIMAX"

class RetrainStatusResponse(BaseModel):
    status: str
    message: str
    new_records_used: int | None = None


class ForecastRequest(BaseModel):
    model_id: Optional[int] = None

class ModelRenameRequest(BaseModel):
    new_model_name: str

# ════════════════════════════════════════════════════════════════════════════
# TAB 2: FORECAST & ACTIONS
# ════════════════════════════════════════════════════════════════════════════

class ForecastGraphPoint(BaseModel):
    date: datetime
    actual: Optional[float] = None          # real booking count (TrainingDataLog)
    predicted: Optional[float] = None       # forward forecast OR backtest fitted value
    lower_bound: Optional[float] = None
    upper_bound: Optional[float] = None
    confidence_tier: Optional[str] = None   # "BACKTEST" | "HIGH" | "LOWER" | None

class ForecastGraphResponse(BaseModel):
    data: List[ForecastGraphPoint]

class CriticalForecastWeek(BaseModel):
    week_start: datetime
    week_end: Optional[datetime] = None       # ← optional with default
    forecasted_volume: int
    risk_factor: str                          # "HIGH" | "MEDIUM" | "LOW"
    confidence_tier: Optional[str] = None    # ← optional with default
    
class ForecastOutlookResponse(BaseModel):
    """
    Single endpoint for all Tab 2 KPI cards + critical weeks table.
    ISO 25010 Performance Efficiency → Time Behavior:
      One query on forecast_cache (indexed on model_id) feeds everything.
    """
    forecasted_bookings_2w: int
    highest_forecast_week_date: datetime
    highest_forecast_week_value: int
    forecasted_bookings_10w: int          # ← FIXED typo + correct field name
    critical_weeks: List[CriticalForecastWeek]   # all 12 forward weeks

# ════════════════════════════════════════════════════════════════════════════
# TAB 3: ADVANCED METRICS — extends existing AdvancedMetricsResponse
# New field: correlation_heatmap added to AdvancedCharts.
# ════════════════════════════════════════════════════════════════════════════

class CorrelationHeatmapPoint(BaseModel):
    variable: str
    correlation: float

class ValidationPoint(BaseModel):
    date_label: str      # "Sep W2"
    actual: int
    forecasted: float
    lower_ci: float
    upper_ci: float


# AdvancedCharts already exists — we need to ADD correlation_heatmap to it.
# Replace the existing AdvancedCharts class with this:
class AdvancedCharts(BaseModel):
    residuals: List[ResidualPoint] = Field(default_factory=list)
    acf: List[CorrelationPoint] = Field(default_factory=list)
    pacf: List[CorrelationPoint] = Field(default_factory=list)
    correlation_heatmap: List[CorrelationHeatmapPoint] = Field(default_factory=list)
    validation_graph: List[ValidationPoint] = Field(default_factory=list)  # ── NEW


class AdvancedMetricsResponse(BaseModel):
    model_params: ModelParams
    statistics: ModelStatistics
    statistical_tests: ModelTests
    charts: AdvancedCharts

# ════════════════════════════════════════════════════════════════════════════
# AUTH & ADMIN — Phase 2
#
# Strict request/response envelopes for the /auth/* surface. EmailStr requires
# the `email-validator` package, which Phase 0 already added to requirements.
#
# DESIGN PRINCIPLES:
#   - Request models reject unknown fields (`model_config = extra="forbid"`).
#     Reason: a frontend bug that sends {"emial": "..."} should fail loudly
#     in development, not silently authenticate against an empty string.
#   - Response models NEVER contain `hashed_password` or any token plaintext
#     beyond the single login/refresh response envelope. The wire shape and
#     the storage shape are decoupled by design.
#   - Error envelopes piggyback on the existing ErrorResponse → ErrorDetail
#     pair already in use by api/main.py. We do NOT introduce a second
#     error format for the auth surface — one envelope, app-wide.
#     ISO 25010 → Maintainability → Analyzability.
# ════════════════════════════════════════════════════════════════════════════

from pydantic import EmailStr, ConfigDict
from uuid import UUID


# ── Login ───────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    """
    Plaintext credentials. Travels over TLS; never logged; never persisted.
    Field-level constraints are deliberately permissive — we let the
    auth_service make the actual reject/accept decision based on hash
    verification, so that an invalid email shape and a wrong password
    return the same opaque 401 (no enumeration).
    """
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class AuthenticatedUser(BaseModel):
    """
    The user-shaped subset that's safe to return on the wire.
    NEVER add `hashed_password` here. Add fields by name to grow.
    """
    id: UUID
    email: EmailStr
    full_name: str
    role: Literal["ADMIN", "ANALYST", "VIEWER"]
    is_active: bool


class LoginResponse(BaseModel):
    """
    What the frontend gets after a successful login.

    `access_expires_at` is included so the frontend can schedule a silent
    refresh slightly BEFORE the token actually expires — preventing the UX
    failure mode where a user clicks "Save" and gets a 401 mid-flight.
    """
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    access_expires_at: datetime
    refresh_expires_at: datetime
    user: AuthenticatedUser


# ── Refresh ─────────────────────────────────────────────────────────────────

class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refresh_token: str = Field(min_length=1, max_length=4096)


class RefreshResponse(BaseModel):
    """
    Refresh response is shaped IDENTICALLY to LoginResponse minus `user`.
    Reason: the user identity hasn't changed, so the frontend doesn't need
    it again. Sending it would invite the frontend to *trust* this payload
    over a fresh /auth/me call — and we want the frontend to treat the JWT
    as the only source of identity truth.
    """
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    access_expires_at: datetime
    refresh_expires_at: datetime


# ── Logout ──────────────────────────────────────────────────────────────────

class LogoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refresh_token: str = Field(min_length=1, max_length=4096)


class LogoutResponse(BaseModel):
    status: Literal["ok"] = "ok"


# ── Register (invite consumption) ───────────────────────────────────────────

class RegisterRequest(BaseModel):
    """
    Submitted when an invitee clicks the invite URL and sets their password.
    The invite_token is the PLAINTEXT half of the (raw, hash) pair generated
    by Phase 0's core.security.generate_invite_token; the server SHA-256s it
    on arrival to find the matching invite_tokens row.
    """
    model_config = ConfigDict(extra="forbid")

    invite_token: str = Field(min_length=20, max_length=128)
    full_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=12, max_length=256)


# RegisterResponse is identical in shape to LoginResponse — we auto-login
# the new user immediately upon successful registration.
RegisterResponse = LoginResponse


# ── Me ──────────────────────────────────────────────────────────────────────

class MeResponse(AuthenticatedUser):
    """
    Returned by GET /auth/me. Frontend calls this on app boot to hydrate
    the auth context. No tokens here — the frontend already has them.
    """
    last_login_at: Optional[datetime] = None
    created_at: datetime

# ════════════════════════════════════════════════════════════════════════════
# ADMIN — Phase 4
#
# Strict request/response envelopes for the /admin/* surface. Each router
# imports only the schemas it needs.
#
# DESIGN PRINCIPLES (carried over from Phase 2):
#   - Request models reject unknown fields (model_config = extra="forbid")
#   - Response models NEVER expose hashed_password, raw tokens, or invite
#     plaintext beyond the single creation response that returns them
#   - Pagination is CURSOR-based on (timestamp DESC, id DESC) for audit
#     and OFFSET-based on bounded page size for users/invites (smaller
#     tables, paginator-friendly)
# ════════════════════════════════════════════════════════════════════════════

from typing import Any
from pydantic import field_validator


# ── Admin: Users ────────────────────────────────────────────────────────────

class AdminUserListItem(BaseModel):
    """User row as seen in the admin table. Excludes hashed_password."""
    id: UUID
    email: EmailStr
    full_name: str
    role: Literal["ADMIN", "ANALYST", "VIEWER"]
    is_active: bool
    last_login_at: Optional[datetime] = None
    created_at: datetime
    created_by_user_id: Optional[UUID] = None


class AdminUserListResponse(BaseModel):
    """Offset-paginated list of users."""
    items: List[AdminUserListItem]
    page: int
    page_size: int
    total: int


class AdminUserDetailResponse(AdminUserListItem):
    """Full detail. Same shape today; reserves room for future fields."""
    updated_at: datetime


class UpdateUserRequest(BaseModel):
    """PATCH body. All fields optional; only provided fields are mutated."""
    model_config = ConfigDict(extra="forbid")

    full_name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    role: Optional[Literal["ADMIN", "ANALYST", "VIEWER"]] = None


class UserStatusResponse(BaseModel):
    """Returned by activate / deactivate. Echoes the resulting state."""
    id: UUID
    email: EmailStr
    is_active: bool


# ── Admin: Invitations ──────────────────────────────────────────────────────

class CreateInvitationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    role: Literal["ADMIN", "ANALYST", "VIEWER"]


class CreateInvitationResponse(BaseModel):
    """
    The plaintext invite_token is returned EXACTLY ONCE — in this response.
    The frontend's responsibility is to display invite_url to the admin
    so they can copy/paste it. After this response, the plaintext is
    gone forever from our side; only its SHA-256 hash exists in the DB.
    """
    invite_id: UUID
    email: EmailStr
    role: Literal["ADMIN", "ANALYST", "VIEWER"]
    invite_url: str
    expires_at: datetime


class InvitationListItem(BaseModel):
    id: UUID
    email: EmailStr
    intended_role: Literal["ADMIN", "ANALYST", "VIEWER"]
    created_by_user_id: UUID
    created_at: datetime
    expires_at: datetime
    consumed_at: Optional[datetime] = None
    consumed_by_user_id: Optional[UUID] = None
    status: Literal["pending", "consumed", "expired", "revoked"]


class InvitationListResponse(BaseModel):
    items: List[InvitationListItem]
    page: int
    page_size: int
    total: int


# ── Admin: Audit ────────────────────────────────────────────────────────────

class AuditLogItem(BaseModel):
    id: int
    timestamp: datetime
    user_id: Optional[UUID] = None
    user_email_snapshot: Optional[str] = None
    action_type: str
    module: str
    target_resource: Optional[str] = None
    status: Literal["SUCCESS", "FAILED"]
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    extra_metadata: Optional[dict[str, Any]] = None


class AuditLogPageResponse(BaseModel):
    """
    Cursor-paginated page of audit rows.

    `next_cursor` is null when there are no more rows. The frontend
    treats it as opaque — just round-trip it as the `?cursor=` parameter
    on the next request. Internally it encodes (last_timestamp, last_id).
    """
    items: List[AuditLogItem]
    next_cursor: Optional[str] = None


class AuditActionTypesResponse(BaseModel):
    """Enumeration of every valid action_type, for the admin filter dropdown."""
    action_types: List[str]
    modules: List[str]


# ── Admin: System Overview ──────────────────────────────────────────────────

class RecentActivityItem(BaseModel):
    id: int
    timestamp: datetime
    actor_email: Optional[str] = None
    action_type: str
    module: str
    status: Literal["SUCCESS", "FAILED"]
    target_resource: Optional[str] = None


class SystemOverviewResponse(BaseModel):
    active_users_count: int
    total_users_count: int
    pending_invitations_count: int
    last_data_sync: Optional[datetime] = None
    last_data_sync_records: Optional[int] = None
    last_forecast_run_at: Optional[datetime] = None
    last_forecast_model_id: Optional[int] = None
    pipeline_status: Literal["healthy", "stale", "unknown"]
    recent_activity: List[RecentActivityItem]


class PipelineStatusResponse(BaseModel):
    """Lightweight subset for non-admin Analyst polling (Phase 5 will allow Analyst access)."""
    last_run_at: Optional[datetime] = None
    last_model_id: Optional[int] = None
    last_status: Literal["SUCCESS", "FAILED", "NEVER_RUN"]


# ── Admin: Global Settings ──────────────────────────────────────────────────

class GlobalSettingItem(BaseModel):
    key: str
    value: Any  # unwrapped from value_json["value"]
    description: Optional[str] = None
    updated_at: datetime
    updated_by_user_id: Optional[UUID] = None


class GlobalSettingsListResponse(BaseModel):
    items: List[GlobalSettingItem]


class UpdateSettingRequest(BaseModel):
    """
    Generic envelope. Per-key validation is in services/settings_service.py
    (Phase 4) — it validates the value's shape AGAINST THE KEY before write.

    Why a generic schema and not one model per key:
      A handful of typed schemas (one per key) would multiply by N every
      time we add a setting. Per-key validation in the service layer
      keeps the API surface small while still rejecting bad values at
      write time. The validator dispatch is one dict lookup.
    """
    model_config = ConfigDict(extra="forbid")
    value: Any

    @field_validator("value")
    @classmethod
    def value_is_jsonable(cls, v: Any) -> Any:
        """Reject obviously-bad values at the schema layer; per-key
        semantic validation happens later in the service."""
        import json
        try:
            json.dumps(v)
        except TypeError as exc:
            raise ValueError(f"value must be JSON-serialisable: {exc}")
        return v