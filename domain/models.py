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
    wmape = Column(Float)
    
    # Training Dates
    train_start_date = Column(DateTime(timezone=True))
    train_end_date = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=get_ph_now)
    ingestion_batch_id = Column(String(36), nullable=True)
    # Relationships
    # Tell the database to destroy the child records when the parent is deleted
    diagnostics = relationship("ModelDiagnostic", back_populates="model", uselist=False, cascade="all, delete-orphan")
    forecasts = relationship("ForecastCache", backref="model", cascade="all, delete-orphan")

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

    weekly_revenue = Column(Float, nullable=True)
    ingestion_batch_id = Column(String(36), nullable=True)


    additional_exog_json = Column(JSON, nullable=True)
    # additional_exog_json is a JSON column that can store any additional exogenous features 
    # that are not explicitly defined in the model. This is a catch-all for future variables.

    # Timestamps
    ingested_at = Column(DateTime(timezone=True), default=get_ph_now)
    
    
# 3. KPI SNAPSHOTS (Page 1 Dashboard Cards)
class DatasetSnapshot(Base):
    __tablename__ = "dataset_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ingestion_batch_id = Column(String(36), nullable=False, unique=True)
    generated_at = Column(DateTime(timezone=True), default=get_ph_now)

    total_transaction_count = Column(Integer)
    total_weekly_records    = Column(Integer)
    total_revenue           = Column(Float, nullable=True)

    data_start_date = Column(DateTime(timezone=True))
    data_end_date   = Column(DateTime(timezone=True))
    span_weeks      = Column(Integer)

    avg_weekly_bookings = Column(Float)
    peak_week_date      = Column(DateTime(timezone=True))
    peak_week_bookings  = Column(Integer)
    growth_rate         = Column(Float)

    bookings_by_year_json  = Column(JSON)
    bookings_by_month_json = Column(JSON)

    holiday_week_count     = Column(Integer)
    non_holiday_week_count = Column(Integer)

    avg_lead_time_days         = Column(Float, nullable=True)
    lead_time_distribution_json = Column(JSON, nullable=True)

    # MODIFIED: now stores year-keyed dict {"overall": [...], "2013": [...]}
    # Old rows with a flat list will return their overall slice gracefully
    # because the endpoint defaults to snapshot.top_airlines_json.get("overall")
    # with a fallback to the raw value.
    top_airlines_json = Column(JSON, nullable=True)

    # ── NEW columns ───────────────────────────────────────────────────────
    top_routes_json      = Column(JSON, nullable=True)  # year-keyed top routes
    revenue_by_year_json = Column(JSON, nullable=True)  # {"overall": float, "2013": float}
    data_quality_json    = Column(JSON, nullable=True)  # year-keyed quality report
    available_years_json = Column(JSON, nullable=True)  # ["2013", "2014", ...]

# 4. DIAGNOSTICS (Page 2 Technical Charts)
class ModelDiagnostic(Base):
    __tablename__ = "model_diagnostics"

    id = Column(Integer, primary_key=True)
    model_id = Column(Integer, ForeignKey("sarimax_models.id"))

    residuals_json = Column(JSON)
    acf_values_json = Column(JSON)
    pacf_values_json = Column(JSON)

    correlation_json = Column(JSON, nullable=True)


    # ADF
    adf_stat = Column(Float)
    adf_pvalue = Column(Float)
    adf_conclusion = Column(String(100))

    # Ljung-Box — ADD STAT AND CONCLUSION
    ljungbox_stat = Column(Float)
    ljungbox_pvalue = Column(Float)
    ljungbox_conclusion = Column(String(100))

    # Jarque-Bera — ADD STAT AND CONCLUSION
    jarquebera_stat = Column(Float)
    jarquebera_pvalue = Column(Float)
    jarquebera_conclusion = Column(String(100))

    validation_graph_json = Column(JSON, nullable=True)

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

    generated_at = Column(DateTime(timezone=True), default=get_ph_now)
    periods_ahead = Column(Integer)
    risk_flag = Column(String(10), nullable=True)

    confidence_tier = Column(String(20), nullable=True)
