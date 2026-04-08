from fastapi import FastAPI, HTTPException, Depends, UploadFile, File
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from typing import List

from services.ingestion_service import ingest_csv

# ── IMPORT OUR NEW STRICT DATA CONTRACTS ──
from api.schemas import (
    ModelDropdownResponse, DashboardStatsResponse, 
    AdvancedMetricsResponse, HistoricalDataResponse, ForecastRequest,
    ModelDropdownItem, HistoricalDataPoint, ModelParams, ModelStatistics, ModelTests, AdvancedCharts
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
    """Page 1: Returns the fast O(1) snapshot metrics for the executive dashboard."""
    snapshot = db.query(ForecastSnapshot).filter(ForecastSnapshot.model_id == model_id).first()
    if not snapshot:
        raise HTTPException(status_code=404, detail="Snapshot not found for this model.")
    
    return DashboardStatsResponse(
        total_records=snapshot.total_records,
        data_quality_pct=snapshot.data_quality_pct,
        revenue_total=snapshot.revenue_total,
        growth_rate=snapshot.growth_rate,
        expected_bookings=snapshot.expected_bookings,
        peak_travel_period=snapshot.peak_travel_period
    )

@app.get("/api/advanced-metrics/{model_id}", response_model=AdvancedMetricsResponse)
def get_advanced_metrics(model_id: int, db: Session = Depends(get_db)):
    """Page 2: Returns the heavy statistical arrays for the MLOps charts."""
    model = db.query(SarimaxModel).filter(SarimaxModel.id == model_id).first()
    diag = db.query(ModelDiagnostic).filter(ModelDiagnostic.model_id == model_id).first()
    
    if not model or not diag:
        raise HTTPException(status_code=404, detail="Model or Diagnostics not found.")
        
    return AdvancedMetricsResponse(
        model_params=ModelParams(
            order=[model.p, model.d, model.q],
            seasonal_order=[model.seasonal_p, model.seasonal_d, model.seasonal_q],
            exogenous_features=model.exog_features_json or []
        ),
        statistics=ModelStatistics(rmse=model.rmse, mae=model.mae, mape=model.mape, adf_status="Stationary"),
        statistical_tests=ModelTests(
            adf_stat=diag.adf_stat, adf_pvalue=diag.adf_pvalue, adf_conclusion="Reject Null",
            ljungbox_pvalue=diag.ljungbox_pvalue, jarquebera_pvalue=diag.jarquebera_pvalue
        ),
        charts=AdvancedCharts(residuals=diag.residuals_json, acf=diag.acf_values_json, pacf=diag.pacf_values_json)
    )

@app.get("/api/historical-data", response_model=HistoricalDataResponse)
def get_historical_ledger(db: Session = Depends(get_db)):
    """Page 3: Returns the raw booking ledger."""
    records = db.query(TrainingDataLog).order_by(TrainingDataLog.record_date.desc()).all()
    points = [
        HistoricalDataPoint(
            date=r.record_date, bookings=r.booking_value, is_holiday=r.is_holiday, weather_indicator=r.weather_indicator
        ) for r in records
    ]
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