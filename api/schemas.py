from pydantic import BaseModel, Field
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
    week_end: datetime
    forecasted_volume: int
    risk_factor: str                        # "HIGH" | "MEDIUM" | "LOW"
    confidence_tier: str                    # so frontend can style HIGH vs LOWER weeks

class ForecastOutlookResponse(BaseModel):
    """
    Single endpoint for all Tab 2 KPI cards + critical weeks table.
    ISO 25010 Performance Efficiency → Time Behavior:
      One query on forecast_cache (indexed on model_id) feeds everything.
    """
    forecasted_bookings_2w: int
    highest_forecast_week_date: datetime
    highest_forecast_week_value: int
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
