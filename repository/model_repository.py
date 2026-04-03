import io
import joblib
from datetime import datetime, timezone
from sqlalchemy import (
    create_engine, Column, Integer, String, Text, LargeBinary, Float, DateTime
)
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# ════════════════════════════════════════════════════════════════════════════
# 1. DATABASE SETUP
# ════════════════════════════════════════════════════════════════════════════

DB_URL = "sqlite:///data/xocompass_models.db"
engine = create_engine(DB_URL, echo=False, future=True)
SessionLocal = sessionmaker(bind=engine)

class Base(DeclarativeBase):
    pass

# ════════════════════════════════════════════════════════════════════════════
# 2. SCHEMA DEFINITION (The "Filing Cabinet")
# ════════════════════════════════════════════════════════════════════════════

class SarimaxModel(Base):
    __tablename__ = "sarimax_models"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    model_name     = Column(String(120), nullable=False, default="xocompass_sarimax")
    model_binary   = Column(LargeBinary, nullable=False) # The joblib blob
    
    # ML Metadata
    pipeline_ver   = Column(String(20), default="v10.1")
    train_end_date = Column(DateTime)
    aic_score      = Column(Float)
    notes          = Column(Text)
    created_at     = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Frontend Dashboard Snapshot Metrics
    total_records      = Column(Integer)
    data_quality_pct   = Column(Float)
    revenue_total      = Column(Float)
    growth_rate_str    = Column(String(50))
    expected_bookings  = Column(Integer)
    peak_travel_period = Column(String(100))

# Create the table if it doesn't exist yet
Base.metadata.create_all(engine)


# ════════════════════════════════════════════════════════════════════════════
# 3. REPOSITORY FUNCTIONS (Called by your API / Services)
# ════════════════════════════════════════════════════════════════════════════

def fetch_all_models_metadata() -> list[dict]:
    """Retrieves basic info for all models to populate the frontend UI dropdown."""
    with SessionLocal() as session:
        models = session.query(SarimaxModel).order_by(SarimaxModel.created_at.desc()).all()
        
        return [
            {
                "id": m.id,
                "version": m.pipeline_ver,
                "train_end_date": m.train_end_date,
                "aic_score": m.aic_score,
                "notes": m.notes
            }
            for m in models
        ]

def fetch_dashboard_stats(model_id: int) -> dict | None:
    """Retrieves the business snapshot metrics saved alongside a specific model."""
    with SessionLocal() as session:
        record = session.query(SarimaxModel).filter(SarimaxModel.id == model_id).first()
        
        if not record:
            return None
            
        return {
            "snapshot_date": record.created_at.strftime("%b %d, %Y") if record.created_at else "N/A",
            "total_records": {
                "value": record.total_records,
                "label": "Transactions Analyzed",
            },
            "data_quality": {
                "value": f"{record.data_quality_pct}%" if record.data_quality_pct else "N/A",
                "label": "Post-cleaning completeness",
            },
            "revenue": {
                "value": f"₱{record.revenue_total:,.1f}" if record.revenue_total else "N/A",
                "label": "Total recognized revenue",
            },
            "growth_rate": {
                "value": record.growth_rate_str,
                "label": "Bookings & revenue",
            },
            "expected_bookings": record.expected_bookings,
            "peak_travel_period": record.peak_travel_period
        }

def fetch_model_binary(model_id: int | None = None) -> tuple:
    """
    Retrieves the raw binary blob and deserializes it back into a SARIMAX object.
    If model_id is None, it fetches the most recently trained model.
    """
    with SessionLocal() as session:
        if model_id:
            record = session.query(SarimaxModel).filter(SarimaxModel.id == model_id).first()
        else:
            record = session.query(SarimaxModel).order_by(SarimaxModel.created_at.desc()).first()
            
        if not record:
            raise ValueError(f"Model ID {model_id} not found in database.")

        # Deserialize the joblib blob back into a statsmodels object
        buffer = io.BytesIO(bytes(record.model_binary))
        sm_model = joblib.load(buffer)
        
        metadata = {"id": record.id, "notes": record.notes}
        
        return sm_model, metadata