from pydantic import BaseModel, Field
from typing import List, Optional, Literal
from datetime import datetime

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
# Endpoint: GET /api/business-analytics
# Source:   business_analytics_snapshots (dataset-scoped, no model_id)
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

class HolidayBreakdown(BaseModel):
    holiday_weeks: int
    non_holiday_weeks: int
    holiday_pct: float

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
    holiday_breakdown: HolidayBreakdown
    
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

class AdvancedCharts(BaseModel):
    residuals: List[ResidualPoint] = Field(default_factory=list)
    acf: List[CorrelationPoint] = Field(default_factory=list)
    pacf: List[CorrelationPoint] = Field(default_factory=list)

class AdvancedMetricsResponse(BaseModel):
    model_params: ModelParams
    statistics: ModelStatistics
    statistical_tests: ModelTests
    charts: AdvancedCharts

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