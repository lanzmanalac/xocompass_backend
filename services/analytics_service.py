"""
services/analytics_service.py
──────────────────────────────
Computes and stages a DatasetSnapshot from training_data_log rows
belonging to a specific ingestion batch.

Called atomically inside ingestion_service.py — never on the request path.
The caller owns db.commit(). This function only stages the write.

ISO 25010:
  Performance Efficiency → Time Behavior:
    All aggregations run once at write time. The GET /api/business-analytics
    request path is a single SELECT with no GROUP BY or SUM.
  Maintainability → Modularity:
    Zero imports from the ML pipeline.
  Reliability → Maturity:
    One snapshot per ingestion batch. Selecting Model #1 always shows
    the dataset that model was trained on — not the latest upload.
"""
from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from domain.models import DatasetSnapshot, TrainingDataLog

logger = logging.getLogger(__name__)
PH_TZ = ZoneInfo("Asia/Manila")


def compute_and_persist_dataset_snapshot(
    db: Session,
    ingestion_batch_id: str,
    avg_lead_time_days: float | None = None,
    lead_time_distribution: list | None = None,
    top_airlines: list | None = None,
) -> DatasetSnapshot:
    """
    Aggregates all training_data_log rows for the given ingestion_batch_id
    and stages a DatasetSnapshot row for the caller to commit.

    Args:
        db: Active SQLAlchemy Session inside an open transaction.
        ingestion_batch_id: UUID identifying this upload batch.

    Returns:
        The staged (not yet committed) DatasetSnapshot ORM object.

    Raises:
        ValueError: If no rows exist for the given batch ID.
    """
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

    # ── Core counts ──────────────────────────────────────────────────────
    total_weekly_records = len(records)
    total_transaction_count = int(sum(r.booking_value or 0 for r in records))

    # ── Revenue ──────────────────────────────────────────────────────────
    revenues = [r.weekly_revenue for r in records if r.weekly_revenue is not None]
    total_revenue = float(sum(revenues)) if revenues else None

    # ── Date coverage ─────────────────────────────────────────────────────
    data_start_date = records[0].record_date
    data_end_date = records[-1].record_date

    # ── Weekly booking statistics ─────────────────────────────────────────
    booking_values = [float(r.booking_value or 0) for r in records]
    avg_weekly_bookings = sum(booking_values) / len(booking_values)
    peak_value = max(booking_values)
    peak_index = booking_values.index(peak_value)
    peak_week_date = records[peak_index].record_date
    peak_week_bookings = int(peak_value)

    # ── Growth rate ───────────────────────────────────────────────────────
    if total_weekly_records >= 104:
        recent = sum(booking_values[-52:])
        prior = sum(booking_values[-104:-52])
        growth_rate = round((recent - prior) / prior * 100, 1) if prior > 0 else 0.0
    elif total_weekly_records >= 52:
        recent = sum(booking_values[-26:])
        prior = sum(booking_values[-52:-26])
        growth_rate = round((recent - prior) / prior * 100, 1) if prior > 0 else 0.0
    else:
        growth_rate = 0.0

    # ── Bookings by year ──────────────────────────────────────────────────
    year_map: dict[str, int] = {}
    for r in records:
        key = str(r.record_date.year)
        year_map[key] = year_map.get(key, 0) + int(r.booking_value or 0)
    bookings_by_year = [
        {"year": yr, "bookings": count}
        for yr, count in sorted(year_map.items())
    ]

    # ── Bookings by month ─────────────────────────────────────────────────
    month_map: dict[str, int] = {}
    for r in records:
        key = r.record_date.strftime("%Y-%m")
        month_map[key] = month_map.get(key, 0) + int(r.booking_value or 0)
    bookings_by_month = [
        {"month": mo, "bookings": count}
        for mo, count in sorted(month_map.items())
    ]

    # ── Holiday breakdown ─────────────────────────────────────────────────
    holiday_week_count = sum(1 for r in records if r.is_holiday)
    non_holiday_week_count = total_weekly_records - holiday_week_count

    # ── Stage the snapshot ────────────────────────────────────────────────
    snapshot = DatasetSnapshot(
        ingestion_batch_id=ingestion_batch_id,
        generated_at=datetime.now(PH_TZ),
        total_transaction_count=total_transaction_count,
        total_weekly_records=total_weekly_records,
        total_revenue=total_revenue,
        data_start_date=data_start_date,
        data_end_date=data_end_date,
        span_weeks=total_weekly_records,
        avg_weekly_bookings=round(avg_weekly_bookings, 2),
        peak_week_date=peak_week_date,
        peak_week_bookings=peak_week_bookings,
        growth_rate=growth_rate,
        bookings_by_year_json=bookings_by_year,
        bookings_by_month_json=bookings_by_month,
        holiday_week_count=holiday_week_count,
        non_holiday_week_count=non_holiday_week_count,
        avg_lead_time_days=avg_lead_time_days,           # ← new
        lead_time_distribution_json=lead_time_distribution,  # ← new
        top_airlines_json=top_airlines,                  # ← new

    )
    db.add(snapshot)

    logger.info(
        "DatasetSnapshot staged: batch=%s, %d weeks, %d transactions, revenue=%s",
        ingestion_batch_id,
        total_weekly_records,
        total_transaction_count,
        f"₱{total_revenue:,.2f}" if total_revenue else "N/A",
    )
    return snapshot