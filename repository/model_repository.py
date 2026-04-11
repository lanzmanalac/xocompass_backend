import io
import joblib
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import os
from dotenv import load_dotenv

# 1. IMPORT YOUR NEW 5-TABLE ARCHITECTURE
from domain.models import Base, SarimaxModel, ForecastSnapshot, ModelDiagnostic, ForecastCache, TrainingDataLog

load_dotenv()
# URL from .env (Neon DB)
SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./xocompass.db")

# Connect to Neon DB
if SQLALCHEMY_DATABASE_URL.startswith("sqlite"):
    engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
else:
    # the path for the Neon Database
    engine = create_engine(SQLALCHEMY_DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 3. REPOSITORY FUNCTIONS
def fetch_all_models_metadata() -> list[dict]:
    """Retrieves basic info for all models to populate the frontend UI dropdown."""
    with SessionLocal() as session:
        models = session.query(SarimaxModel).order_by(SarimaxModel.created_at.desc()).all()
        return [
            {
                "id": m.id,
                "version": m.pipeline_ver,
                "train_end_date": m.train_end_date,
                "aic_score": m.aic_score
            }
            for m in models
        ]

def fetch_dashboard_stats(model_id: int) -> dict | None:
    """Retrieves the business snapshot metrics saved alongside a specific model."""
    with SessionLocal() as session:
        record = session.query(ForecastSnapshot).filter(ForecastSnapshot.model_id == model_id).order_by(ForecastSnapshot.generated_at.desc()).first()
        
        if not record:
            return None
            
        return {
            "total_records": record.total_records,
            "data_quality": record.data_quality_pct,
            "revenue": record.revenue_total,
            "growth_rate": record.growth_rate,
            "expected_bookings": record.expected_bookings,
            "peak_travel_period": record.peak_travel_period
        }

def fetch_model_binary(model_id: int | None = None) -> tuple:
    """
    WARNING: This function needs to be rewritten in Day 3 to load from a file path, 
    not a BLOB. Left as placeholder to prevent API crashes.
    """
    pass