from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime

# ════════════════════════════════════════════════════════════════════════════
# SHARED UTILITY SCHEMAS
# ════════════════════════════════════════════════════════════════════════════

class ModelDropdownItem(BaseModel):
    id: int
    version: str
    train_end_date: Optional[datetime] = None
    aic_score: Optional[float] = None
    notes: Optional[str] = None

class ModelDropdownResponse(BaseModel):
    available_models: List[ModelDropdownItem]

# ════════════════════════════════════════════════════════════════════════════
# PAGE 1: SIMPLIFIED METRICS (Executive Dashboard)
# ════════════════════════════════════════════════════════════════════════════

class DashboardStatsResponse(BaseModel):
    total_records: int
    data_quality_pct: float
    revenue_total: float
    growth_rate: float
    expected_bookings: int
    peak_travel_period: str

class ForecastGraphPoint(BaseModel):
    date: datetime
    actual: Optional[float] = None
    predicted: Optional[float] = None
    lower_bound: Optional[float] = None
    upper_bound: Optional[float] = None

class ForecastGraphResponse(BaseModel):
    data: List[ForecastGraphPoint]

# ════════════════════════════════════════════════════════════════════════════
# PAGE 2: ADVANCED METRICS (MLOps View)
# ════════════════════════════════════════════════════════════════════════════

class ModelParams(BaseModel):
    order: List[int]          # [p, d, q]
    seasonal_order: List[int] # [P, D, Q]
    exogenous_features: List[str]

class ModelStatistics(BaseModel):
    rmse: float
    mae: float
    mape: float
    wmape: Optional[float] = None  # Optional since it might be added later
    
class ModelTests(BaseModel):
    adf_stat: float
    adf_pvalue: float
    adf_conclusion: str
    ljungbox_pvalue: float
    jarquebera_pvalue: float

class AdvancedCharts(BaseModel):
    residuals: List[Dict[str, float]] # e.g. [{"fitted": 10.5, "residual": -0.2}]
    acf: List[float]
    pacf: List[float]

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
    exogenous_factors: List[str]
    target_variable: str = "Booking Date" # Hardcoded per UI specs

class ForecastRequest(BaseModel):
    model_id: Optional[int] = None