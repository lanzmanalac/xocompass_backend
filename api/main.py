from fastapi import FastAPI, HTTPException, Depends, UploadFile, File
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from typing import List
from pydantic import BaseModel

from services.ingestion_service import ingest_csv

# ── IMPORT OUR NEW STRICT DATA CONTRACTS ──
from api.schemas import (
    ModelDropdownResponse, DashboardStatsResponse, 
    AdvancedMetricsResponse, HistoricalDataResponse, ForecastRequest,
    ModelDropdownItem, HistoricalDataPoint, ModelParams, ModelStatistics, ModelTests, AdvancedCharts,
    ForecastGraphPoint,
    ForecastGraphResponse,
    StrategicActionsResponse,
    StrategicAction,
    RetrainRequest,
    RetrainStatusResponse,
    ChartPoint,
)

# ── IMPORT DATABASE ARCHITECTURE ──
from repository.model_repository import SessionLocal
from domain.models import SarimaxModel, ForecastSnapshot, ModelDiagnostic, TrainingDataLog, ForecastCache

app = FastAPI(title="XoCompass API", version="10.1")

# ════════════════════════════════════════════════════════════════════════════
# 0. CONFIGURATION & DEPENDENCIES
# ════════════════════════════════════════════════════════════════════════════

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173", "https://xocompass.vercel.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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

# ════════════════════════════════════════════════════════════════════════════
# 1. PRESENTATION LAYER ENDPOINTS (Wired to Mock Data)
# ════════════════════════════════════════════════════════════════════════════

@app.get("/api/models", response_model=ModelDropdownResponse)
def get_model_registry(db: Session = Depends(get_db)):
    """Page 2: Populates the model selection dropdown."""
    models = db.query(SarimaxModel).all()
    items = [
        ModelDropdownItem(
            id=m.id, version=m.pipeline_ver, train_end_date=m.train_end_date, aic_score=m.aic_score
        ) for m in models
    ]
    return ModelDropdownResponse(available_models=items)

@app.get("/api/dashboard-stats/{model_id}", response_model=DashboardStatsResponse)
def get_dashboard_stats(model_id: int, db: Session = Depends(get_db)):
    """Page 1: Returns the fast snapshot metrics for the dashboard."""
    snapshot = db.query(ForecastSnapshot).filter(ForecastSnapshot.model_id == model_id).first()
    
    if not snapshot:
        raise HTTPException(status_code=404, detail="Snapshot not found for this model.")
    
    return DashboardStatsResponse(
        total_records=snapshot.total_records,
        data_quality_pct=snapshot.data_quality_pct,
        revenue_total=snapshot.revenue_total,
        growth_rate=snapshot.growth_rate,
        expected_bookings=snapshot.expected_bookings,
        peak_travel_period=snapshot.peak_travel_period,
        # Injecting the mock data so the frontend chart can render
        bookings_forecast=[
            { "month": "Jan", "actual": 280, "predicted": 295, "lowerCI": 260, "upperCI": 330 },
            { "month": "Feb", "actual": 310, "predicted": 320, "lowerCI": 290, "upperCI": 350 },
            { "month": "Mar", "actual": 340, "predicted": 355, "lowerCI": 320, "upperCI": 390 },
            { "month": "Apr", "actual": 360, "predicted": 380, "lowerCI": 345, "upperCI": 420 },
            { "month": "May", "actual": 395, "predicted": 410, "lowerCI": 370, "upperCI": 450 },
            { "month": "Jun", "actual": 420, "predicted": 435, "lowerCI": 395, "upperCI": 470 }
        ]
            
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
            acf=[CorrelationPoint(**p) for p in (diag.acf_values_json or [])],
            pacf=[CorrelationPoint(**p) for p in (diag.pacf_values_json or [])]
        )
    )

@app.get("/api/historical-data", response_model=HistoricalDataResponse)
def get_historical_ledger(db: Session = Depends(get_db)):
    records = db.query(TrainingDataLog).order_by(
        TrainingDataLog.record_date.asc()
    ).all()

    points = []
    for r in records:
        exog = r.additional_exog_json or {}
        points.append(HistoricalDataPoint(
        date=r.record_date,
        bookings=r.booking_value,
        is_holiday=r.is_holiday,
        weather_indicator=r.weather_indicator
    ))

    return HistoricalDataResponse(data=points)

@app.post("/api/upload")
async def upload_pos_data(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Page 3: Safely ingests KJS CSV data and prevents duplicate dates."""
    if not file.filename.endswith('.csv'):
        raise HTTPException(status_code=400, detail="Only CSV files are allowed.")
        
    contents = await file.read()
    try:
        result = ingest_csv(contents, db)
        return result
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {str(e)}")

@app.get("/api/forecast-graph/{model_id}", response_model=ForecastGraphResponse)
def get_forecast_graph(model_id: int, db: Session = Depends(get_db)):
    """
    Page 1: Actual vs Predicted graph.
    Returns historical actuals from training_data_log +
    future predictions from forecast_cache.
    """
    # Historical actuals
    actuals = db.query(TrainingDataLog).order_by(
        TrainingDataLog.record_date.asc()
    ).all()

    # Cached predictions for this model
    predictions = db.query(ForecastCache).filter(
        ForecastCache.model_id == model_id
    ).order_by(ForecastCache.forecast_date.asc()).all()

    points = []

    # Actual weeks — predicted is None
    for r in actuals:
        points.append(ForecastGraphPoint(
            date=r.record_date,
            actual=r.booking_value,
            predicted=None,
            lower_bound=None,
            upper_bound=None
        ))

    # Future weeks — actual is None
    for p in predictions:
        points.append(ForecastGraphPoint(
            date=p.forecast_date,
            actual=None,
            predicted=p.predicted,
            lower_bound=p.lower_bound,
            upper_bound=p.upper_bound
        ))

    return ForecastGraphResponse(data=points)

@app.get("/api/strategic-actions/{model_id}", 
         response_model=StrategicActionsResponse)
def get_strategic_actions(model_id: int, db: Session = Depends(get_db)):
    """
    Page 1: Prescriptive analytics.
    Derives actionable recommendations from forecast_cache data.
    Rule-based engine — no ML required.
    """
    snapshot = db.query(ForecastSnapshot).filter(
        ForecastSnapshot.model_id == model_id
    ).first()

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
async def trigger_retrain(
    request: RetrainRequest,
    db: Session = Depends(get_db)
):
    """
    Page 3: Triggers model retraining.
    Phase 1: Returns a stub response.
    Phase 2 (Block B): Replace stub with real pipeline call.
    """
    # COUNT new records available since last training
    last_model = db.query(SarimaxModel).filter(
        SarimaxModel.is_active == True
    ).first()

    new_record_count = db.query(TrainingDataLog).count()

    # STUB — replace this block in Block B
    return RetrainStatusResponse(
        status="queued",
        message="Retraining pipeline is being initialized. "
                "This is a Phase 1 stub — real training coming in Block B.",
        new_records_used=new_record_count
    )