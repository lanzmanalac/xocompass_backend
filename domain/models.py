# domain/models.py
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Text, LargeBinary, Float, DateTime
from sqlalchemy.orm import DeclarativeBase

# Base class for SQLAlchemy ORM
class Base(DeclarativeBase):
    pass

# ════════════════════════════════════════════════════════════════════════════
# DOMAIN ENTITY: SarimaxModel
# ════════════════════════════════════════════════════════════════════════════
class SarimaxModel(Base):
    """
    Represents the database schema for a trained XoCompass forecast model.
    Contains both the binary ML algorithm and the business KPI snapshot.
    """
    __tablename__ = "sarimax_models"

    # ── Identifiers ──
    id             = Column(Integer, primary_key=True, autoincrement=True)
    model_name     = Column(String(120), nullable=False, default="xocompass_sarimax")
    
    # ── The ML Brain ──
    model_binary   = Column(LargeBinary, nullable=False) # The joblib compressed blob
    
    # ── Model Metadata ──
    pipeline_ver   = Column(String(20), default="v10.1")
    train_end_date = Column(DateTime)
    aic_score      = Column(Float)
    notes          = Column(Text)
    created_at     = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # ── Frontend Dashboard Snapshot Metrics ──
    total_records      = Column(Integer)
    data_quality_pct   = Column(Float)
    revenue_total      = Column(Float)
    growth_rate_str    = Column(String(50))
    expected_bookings  = Column(Integer)
    peak_travel_period = Column(String(100))

    def __repr__(self):
        return f"<SarimaxModel(id={self.id}, version={self.pipeline_ver}, created={self.created_at})>"