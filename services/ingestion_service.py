"""Replace the native booking ledger and stage its analytics snapshot together."""

import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from core.config import MANILA_TIMEZONE
from domain.models import TrainingDataLog
from services.analytics_service import compute_and_persist_dataset_snapshot
from services.booking_analytics import prepare_booking_data


def ingest_csv(file_bytes: bytes, db: Session) -> dict:
    """Validate the full-history upload before replacing the active dataset."""
    prepared = prepare_booking_data(file_bytes)
    ingestion_batch_id = str(uuid.uuid4())
    db_rows = [
        TrainingDataLog(
            record_date=record.record_date.replace(tzinfo=MANILA_TIMEZONE),
            booking_value=float(record.booking_value),
            is_holiday=record.is_holiday,
            weather_indicator=0.0,
            weekly_revenue=record.weekly_revenue,
            additional_exog_json={"is_long_weekend": int(record.is_long_weekend)},
            ingestion_batch_id=ingestion_batch_id,
        )
        for record in prepared.weekly_records
    ]

    # Replace the ledger and save its snapshot in one transaction.
    try:
        # Serialize PostgreSQL replacements while allowing ordinary reads.
        if db.get_bind().dialect.name == "postgresql":
            db.execute(text("LOCK TABLE training_data_log IN SHARE ROW EXCLUSIVE MODE"))
        db.query(TrainingDataLog).delete()
        db.add_all(db_rows)
        db.flush()
        compute_and_persist_dataset_snapshot(
            db,
            ingestion_batch_id,
            **prepared.snapshot_metadata,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    return {
        "status": "success",
        "message": f"Ingested {len(db_rows)} new weekly records.",
        "new_records": len(db_rows),
        "revenue_total": prepared.revenue_total,
        "ingestion_batch_id": ingestion_batch_id,
        "avg_lead_time_days": prepared.snapshot_metadata["avg_lead_time_days"],
    }
