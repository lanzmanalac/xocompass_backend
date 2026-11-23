"""Persist analytical aggregates for the native booking ledger."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from domain.models import DatasetSnapshot, TrainingDataLog
from services.booking_analytics import dataset_snapshot_fields

logger = logging.getLogger(__name__)


def compute_and_persist_dataset_snapshot(
    db: Session,
    ingestion_batch_id: str,
    avg_lead_time_days: float | None = None,
    lead_time_distribution: list | None = None,
    lead_time_by_year: dict | None = None,
    top_airlines_by_year: dict | None = None,
    top_routes_by_year: dict | None = None,
    revenue_by_year: dict | None = None,
    data_quality_by_year: dict | None = None,
    available_years: list | None = None,
) -> DatasetSnapshot:
    """Stage a snapshot for the caller's existing ingestion transaction."""
    records = (
        db.query(TrainingDataLog)
        .filter(TrainingDataLog.ingestion_batch_id == ingestion_batch_id)
        .order_by(TrainingDataLog.record_date.asc())
        .all()
    )

    if not records:
        raise ValueError(
            f"Cannot compute dataset snapshot: no rows found for "
            f"ingestion_batch_id={ingestion_batch_id}."
        )

    fields = dataset_snapshot_fields(
        records,
        avg_lead_time_days=avg_lead_time_days,
        lead_time_distribution=lead_time_distribution,
        lead_time_by_year=lead_time_by_year,
        top_airlines_by_year=top_airlines_by_year,
        top_routes_by_year=top_routes_by_year,
        revenue_by_year=revenue_by_year,
        data_quality_by_year=data_quality_by_year,
        available_years=available_years,
    )
    snapshot = DatasetSnapshot(ingestion_batch_id=ingestion_batch_id, **fields)
    db.add(snapshot)
    logger.info(
        "DatasetSnapshot staged: batch=%s, %d weeks, %d transactions",
        ingestion_batch_id,
        fields["total_weekly_records"],
        fields["total_transaction_count"],
    )
    return snapshot
