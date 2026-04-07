# domain/models.py
from datetime import datetime
from zoneinfo import ZoneInfo
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text, JSON
from sqlalchemy.orm import DeclarativeBase, relationship

class Base(DeclarativeBase):
    pass

def get_ph_now():
    """
        Explicitly store database times in Philippine Standard Time (GMT+8).
    """
    return datetime.now(ZoneInfo("Asia/Manila"))

# 1. MODEL REGISTRY (Page 2 Dropdown & Metadata)
class SarimaxModel(Base):
    __tablename__ = "sarimax_models"
    id = Column(Integer, primary_key=True, autoincrement=True)
    model_name = Column(String(120), nullable=False)
    model_path = Column(String(255), nullable=False)  # Path to .joblib, NOT a BLOB
    is_active = Column(Boolean, default=False)
    pipeline_ver = Column(String(20), default="v10.1")
    
    # Orders (p,d,q)(P,D,Q)m
    p = Column(Integer)
    d = Column(Integer)
    q = Column(Integer)
    seasonal_p = Column(Integer)
    seasonal_d = Column(Integer)
    seasonal_q = Column(Integer)

    exog_features_json = Column(JSON) # e.g. ["is_holiday", "typhoon_msw"]
    
    # Metrics
    aic_score = Column(Float)
    bic_score = Column(Float)

    mae = Column(Float)
    rmse = Column(Float)
    mape = Column(Float)
    
    # Training Dates
    train_start_date = Column(DateTime(timezone=True))
    train_end_date = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=get_ph_now)

    # Relationships
    diagnostics = relationship("ModelDiagnostic", back_populates="model", uselist=False)

# 2. THE LEDGER (Page 3 History & Uploads)
class TrainingDataLog(Base):
    __tablename__ = "training_data_log"
    id = Column(Integer, primary_key=True)
    record_date = Column(DateTime(timezone=True), index=True)
    
    # Target Variable
    booking_value = Column(Float)
    
    # Exogenous Features
    is_holiday = Column(Boolean, default=False)
    weather_indicator = Column(Float, nullable=True) # typhoon_msw
    additional_exog_json = Column(JSON, nullable=True)
    # additional_exog_json is a JSON column that can store any additional exogenous features 
    # that are not explicitly defined in the model. This is a catch-all for future variables.

    # Timestamps
    ingested_at = Column(DateTime(timezone=True), default=get_ph_now)
    
    
# 3. KPI SNAPSHOTS (Page 1 Dashboard Cards)
class ForecastSnapshot(Base):
    __tablename__ = "forecast_snapshots"
    id = Column(Integer, primary_key=True)
    model_id = Column(Integer, ForeignKey("sarimax_models.id"))
    generated_at = Column(DateTime(timezone=True), default=get_ph_now)
    total_records = Column(Integer)
    data_quality_pct = Column(Float)
    revenue_total = Column(Float)
    growth_rate = Column(Float)
    expected_bookings = Column(Integer)
    peak_travel_period = Column(String(100))

# 4. DIAGNOSTICS (Page 2 Technical Charts)
class ModelDiagnostic(Base):
    __tablename__ = "model_diagnostics"
    id = Column(Integer, primary_key=True)
    model_id = Column(Integer, ForeignKey("sarimax_models.id"))
    residuals_json = Column(JSON)   # For Residuals vs Fitted graph
    acf_values_json = Column(JSON)  # For ACF graph
    pacf_values_json = Column(JSON) # For PACF graph
    
    # Test Stats
    adf_stat = Column(Float)
    adf_pvalue = Column(Float)

    
    ljungbox_pvalue = Column(Float)
    jarquebera_pvalue = Column(Float)

    # Relationships
    model = relationship("SarimaxModel", back_populates="diagnostics")

# 5. FORECAST CACHE (Page 1 Graph)
class ForecastCache(Base):
    __tablename__ = "forecast_cache"
    id = Column(Integer, primary_key=True)
    model_id = Column(Integer, ForeignKey("sarimax_models.id"))
    forecast_date = Column(DateTime(timezone=True))
    predicted = Column(Float)
    lower_bound = Column(Float)
    upper_bound = Column(Float)