import io
import joblib
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import os
from pathlib import Path
from dotenv import load_dotenv

# 1. IMPORT YOUR NEW 5-TABLE ARCHITECTURE
from domain.models import Base, SarimaxModel, ModelDiagnostic, ForecastCache, TrainingDataLog

load_dotenv()
# URL from .env (Neon DB)
SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./xocompass.db")

# Connect to Neon DB
if SQLALCHEMY_DATABASE_URL.startswith("sqlite"):
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL, 
        connect_args={"check_same_thread": False}
    )
else:
    # the path for the Neon Database
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=2,
        pool_recycle=300,
    )

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
    with SessionLocal() as session:
        if model_id:
            record = session.query(SarimaxModel).filter(
                SarimaxModel.id == model_id
            ).first()
        else:
            record = session.query(SarimaxModel).filter(
                SarimaxModel.is_active == True
            ).first()
        if not record:
            raise ValueError("No active model found. Run the orchestrator first.")
        
        path = Path(record.model_path)
        if not path.exists():
            raise FileNotFoundError(
                f"Model file missing at {path}. Retrain to regenerate."
            )
        
        return joblib.load(path), {
            "model_id": record.id,
            "train_end_date": record.train_end_date,
            "exog_features": record.exog_features_json or [],
        }