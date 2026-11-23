"""Prepare booking and Business Analytics aggregates without database dependencies."""

from __future__ import annotations

import csv
from calendar import monthrange
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from io import BytesIO, TextIOWrapper
from types import SimpleNamespace
from typing import Any, Protocol

import pandas as pd

from core.config import (
    MANILA_TIMEZONE,
    WEEKLY_RULE,
    current_booking_week_start,
    find_booking_date_column,
    parse_manila_timestamps,
)
from core.exogenous import PHHolidayEngine


class BookingCSVError(ValueError):
    """A booking CSV validation message safe to display to the user."""


@dataclass(frozen=True, slots=True)
class WeeklyBooking:
    """A completed Manila reporting week, with a local naive start date."""

    record_date: datetime
    booking_value: int
    weekly_revenue: float | None
    is_holiday: bool
    is_long_weekend: bool


class WeeklyRecord(Protocol):
    """Interface shared by weekly browser aggregates and persisted ledger rows."""

    record_date: datetime
    booking_value: float | int | None
    weekly_revenue: float | None
    is_holiday: bool


@dataclass(frozen=True, slots=True)
class PreparedBookingData:
    """The dates, ledger aggregates and snapshot metadata from one upload."""

    training_dates: list[str]
    weekly_records: list[WeeklyBooking]
    revenue_total: float | None
    snapshot_metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class BrowserBookings:
    """A browser-ready local run input with JSON-compatible analytics views."""

    training_dates: list[str]
    weekly_records: list[WeeklyBooking]
    business_analytics: dict[str, dict[str, Any]]
    data_through: str


def _compute_top_n_by_year(
    series: pd.Series,      #   series      — the raw pandas Series (e.g., df['airline_code'])
    year_series: pd.Series, #   year_series — the year for each row (e.g., df['date'].dt.year)
    key_name: str,          #   key_name    — what to call the item key in the output dict
    top_n: int = 7,         #   top_n       — how many items to return per slice (default 7)
) -> dict:
    def _top_n_from_series(s: pd.Series) -> list:
        counts = (
            s.dropna()
            .astype(str)
            .str.strip()
            .value_counts()
            .head(top_n)
        )
        total = int(counts.sum())
        return [
            {
                key_name: code,
                "count": int(cnt),
                "pct": round(float(cnt) / total * 100, 1) if total > 0 else 0.0,
            }
            for code, cnt in counts.items()
        ]

    result = {"overall": _top_n_from_series(series)}

    for year in sorted(year_series.dropna().unique()):
        mask = year_series == year
        result[str(int(year))] = _top_n_from_series(series[mask])

    return result

def _compute_data_quality(df: pd.DataFrame, year_col: pd.Series) -> dict:

    def _quality_for_slice(slice_df: pd.DataFrame) -> dict:
        total = len(slice_df)
        if total == 0:
            return {
                "total_rows": 0,
                "duplicate_rows": 0,
                "missing_airline": 0,
                "missing_route": 0,
                "missing_travel_date": 0,
                "invalid_travel_date": 0,
                "missing_revenue": 0,
                "quality_score_pct": 0.0,
            }

        dup_subset = [c for c in ["date", "_airline_clean", "_route_clean"] if c in slice_df.columns]
        duplicate_rows = int(slice_df.duplicated(subset=dup_subset).sum()) if dup_subset else 0

        missing_airline = int(slice_df["_airline_clean"].isna().sum()) if "_airline_clean" in slice_df.columns else total
        missing_route   = int(slice_df["_route_clean"].isna().sum())   if "_route_clean"   in slice_df.columns else total

        if "travel_date" in slice_df.columns:
            missing_travel_date = int(slice_df["travel_date"].isna().sum())
            invalid_travel_date = int(
                ((slice_df["travel_date"] - slice_df["date"]).dt.days < 0)
                .sum()
            ) if "travel_date" in slice_df.columns else 0
        else:
            missing_travel_date = total
            invalid_travel_date = 0

        missing_revenue = int(slice_df["_revenue_cleaned"].isna().sum()) if "_revenue_cleaned" in slice_df.columns else total

        dirty_mask = pd.Series(False, index=slice_df.index)
        if dup_subset:
            dirty_mask |= slice_df.duplicated(subset=dup_subset)
        if "_airline_clean" in slice_df.columns:
            dirty_mask |= slice_df["_airline_clean"].isna()
        if "_route_clean" in slice_df.columns:
            dirty_mask |= slice_df["_route_clean"].isna()
        if "travel_date" in slice_df.columns:
            dirty_mask |= slice_df["travel_date"].isna()
            dirty_mask |= (slice_df["travel_date"] - slice_df["date"]).dt.days < 0
        if "_revenue_cleaned" in slice_df.columns:
            dirty_mask |= slice_df["_revenue_cleaned"].isna()

        clean_rows = int((~dirty_mask).sum())
        quality_score_pct = round(clean_rows / total * 100, 1) if total > 0 else 0.0

        return {
            "total_rows": total,
            "duplicate_rows": duplicate_rows,
            "missing_airline": missing_airline,
            "missing_route": missing_route,
            "missing_travel_date": missing_travel_date,
            "invalid_travel_date": invalid_travel_date,
            "missing_revenue": missing_revenue,
            "quality_score_pct": quality_score_pct,
        }

    result = {"overall": _quality_for_slice(df)}

    for year in sorted(year_col.dropna().unique()):
        mask = year_col == year
        result[str(int(year))] = _quality_for_slice(df[mask])

    return result

def _read_full_history_csv(file_bytes: bytes) -> pd.DataFrame:
    with TextIOWrapper(BytesIO(file_bytes), encoding="utf-8-sig", newline="") as source:
        rows = csv.reader(source, strict=True)
        width = None
        try:
            for row in rows:
                if not row or (len(row) == 1 and not row[0].strip()):
                    continue
                if width is None:
                    width = len(row)
                elif len(row) != width:
                    raise BookingCSVError(f"CSV row {rows.line_num} does not match the column headers.")
        except csv.Error as exc:
            raise pd.errors.ParserError("Malformed CSV. Correct the file and upload again.") from exc
        source.seek(0)
        return pd.read_csv(source)

def prepare_booking_data(
    file_bytes: bytes, *, cutoff: pd.Timestamp | None = None, browser_safe_labels: bool = False
) -> PreparedBookingData:
    """Validate a full-history upload and prepare its reporting-week ledger."""

    avg_lead_time_days = None
    lead_time_distribution = None
    lead_time_by_year = None

    df = _read_full_history_csv(file_bytes)

    date_col = find_booking_date_column(df.columns)
    if not date_col:
        raise BookingCSVError("No booking date column found. Use Generation Date, Booking Date, or Date.")

    normalized_cols = {c: c.strip().lower() for c in df.columns}
    revenue_col = next(
        (original for original, normalized in normalized_cols.items()
         if "net amount" in normalized),
        None,
    )

    try:
        df["date"] = parse_manila_timestamps(df[date_col])
    except ValueError:
        raise BookingCSVError("CSV booking dates must use a consistent timezone.") from None
    if df["date"].isna().any():
        raise BookingCSVError("CSV contains an invalid booking date. Correct the file and upload again.")

    reporting_cutoff = current_booking_week_start() if cutoff is None else cutoff
    df = df.loc[df["date"] < reporting_cutoff].copy()
    if df.empty:
        raise BookingCSVError("CSV contains no bookings in completed reporting weeks.")
    df["_year"] = df["date"].dt.year

    travel_col = next(
        (c for c in df.columns if 'travel' in c.lower() and 'date' in c.lower()),
        None,
    )

    airline_col = next(
        (c for c in df.columns if 'airline' in c.lower()),
        None,
    )

    route_col = next(
        (c for c in df.columns
         if any(kw in c.lower() for kw in ['route', 'sector'])),
        None,
    )
    origin_col = next(
        (c for c in df.columns if any(kw in c.lower() for kw in ['origin', 'from', 'departure'])),
        None,
    )
    dest_col = next(
        (c for c in df.columns if any(kw in c.lower() for kw in ['destination', 'dest', 'to', 'arrival'])),
        None,
    )
    if route_col:
        # Pandas 3 preserves nulls on string conversion. Keep the native ledger's legacy route value.
        df['_route_clean'] = df[route_col].astype(str).fillna("nan").str.strip().str.upper()
    elif origin_col and dest_col:
        df['_route_clean'] = (
            df[origin_col].astype(str).str.strip().str.upper()
            + "-"
            + df[dest_col].astype(str).str.strip().str.upper()
        )
        df.loc[
            df['_route_clean'].str.contains(r'(^NAN-|^NAN$|-NAN$)', regex=True, na=False),
            '_route_clean'
        ] = None
    else:
        df['_route_clean'] = None

    if airline_col:
        df['_airline_clean'] = (
            df[airline_col]
            .astype(str)
            .str.strip()
            .replace({'nan': None, 'NaN': None, '': None})
        )
    else:
        df['_airline_clean'] = None

    if travel_col:
        df['travel_date'] = parse_manila_timestamps(df[travel_col])
        df['lead_time'] = (df['travel_date'] - df['date']).dt.days

        valid_lead = df['lead_time'].dropna()
        valid_lead = valid_lead[valid_lead >= 0]

        if len(valid_lead) > 0:
            avg_lead_time_days = round(float(valid_lead.mean()), 1)

            def _bucket(series):
                bins = [
                    (0,   7,   "0-7 days"),
                    (8,   14,  "8-14 days"),
                    (15,  30,  "15-30 days"),
                    (31,  60,  "31-60 days"),
                    (61,  90,  "61-90 days"),
                    (91,  float("inf"), "90+ days"),
                ]
                return [
                    {"bucket": label, "count": int(((series >= low) & (series <= high)).sum())}
                    for low, high, label in bins
                ]

            lead_time_distribution = _bucket(valid_lead)

            lead_time_by_year = {}
            valid_df_for_year = df[
                df["lead_time"].notna() & (df["lead_time"] >= 0)
            ].copy()

            for yr, grp in valid_df_for_year.groupby(valid_df_for_year["date"].dt.year):
                yr_lead = grp["lead_time"]
                lead_time_by_year[str(int(yr))] = {
                    "avg": round(float(yr_lead.mean()), 1),
                    "distribution": _bucket(yr_lead),
                }

    if revenue_col:
        raw_rev     = df[revenue_col].astype(str)
        is_negative = raw_rev.str.strip().str.startswith("(")
        cleaned     = raw_rev.str.replace(r"[^\d.]", "", regex=True)
        numeric_rev = pd.to_numeric(cleaned, errors="coerce")
        numeric_rev = numeric_rev.where(~is_negative, other=-numeric_rev)
        df["_revenue_cleaned"] = numeric_rev

        revenue_by_year = {}
        for year, grp in df.groupby("_year"):
            revenue_by_year[str(int(year))] = round(float(grp["_revenue_cleaned"].sum()), 2)
    else:
        df["_revenue_cleaned"] = None
        revenue_by_year = None  # ── NEW

    airline_series = df['_airline_clean']
    route_series = df['_route_clean']
    if browser_safe_labels:
        # Browser snapshots can be published. Retain only codes from explicit code columns.
        if normalized_cols.get(airline_col) in {"airline", "airline code", "airline_code"}:
            airline_series = airline_series.where(airline_series.str.fullmatch(
                r"(?i)(?:[A-Z][A-Z0-9]|[0-9][A-Z]|[A-Z]{3})", na=False
            ))
        else:
            airline_series = pd.Series(dtype=object)
        if normalized_cols.get(route_col) in {"route", "sector", "flight route", "route code"}:
            route_series = route_series.where(route_series.str.fullmatch(r"[A-Z]{3}-[A-Z]{3}", na=False))
        elif (
            normalized_cols.get(origin_col) in {"origin", "origin airport", "from airport", "departure airport"}
            and normalized_cols.get(dest_col) in {"destination", "destination airport", "to airport", "arrival airport"}
        ):
            route_series = route_series.where(route_series.str.fullmatch(r"[A-Z]{3}-[A-Z]{3}", na=False))
        else:
            route_series = pd.Series(dtype=object)

    if airline_col and (not browser_safe_labels or airline_series.notna().any()):
        top_airlines_by_year = _compute_top_n_by_year(
            series=airline_series,
            year_series=df['_year'],
            key_name="airline_code",
            top_n=7,
        )
    else:
        top_airlines_by_year = None

    has_routes = route_series.notna().any()

    if has_routes:
        top_routes_by_year = _compute_top_n_by_year(
            series=route_series,
            year_series=df['_year'],
            key_name="route",
            top_n=10,
        )
    else:
        top_routes_by_year = None

    data_quality_by_year = _compute_data_quality(df, df['_year'])

    available_years = sorted([str(int(y)) for y in df['_year'].dropna().unique()])

    df["week"] = df["date"].dt.to_period(WEEKLY_RULE).dt.start_time
    weekly_counts = df.groupby("week").size().reset_index(name="booking_value")

    if revenue_col:
        weekly_revenue = df.groupby("week")["_revenue_cleaned"].sum().reset_index(name="weekly_revenue")
        weekly = weekly_counts.merge(weekly_revenue, on="week", how="left")
        total_revenue = float(df["_revenue_cleaned"].sum())
    else:
        weekly = weekly_counts.copy()
        weekly["weekly_revenue"] = None
        total_revenue = None

    holidays_df = PHHolidayEngine().generate(
        weekly["week"].min() - timedelta(days=7),
        weekly["week"].max() + timedelta(days=7)
    )

    weekly_records = []
    for _, row in weekly.iterrows():
        dt_naive = row["week"]
        wk_end = dt_naive + timedelta(days=6)

        sl = holidays_df.reindex(
            pd.date_range(dt_naive, wk_end, freq="D")
        ).fillna(0)

        is_hol = bool(sl["is_holiday"].max() > 0)
        is_lw  = bool(sl["is_long_weekend"].max() > 0)

        weekly_records.append(WeeklyBooking(
            record_date=dt_naive.to_pydatetime(),
            booking_value=int(row["booking_value"]),
            weekly_revenue=float(row["weekly_revenue"]) if row["weekly_revenue"] is not None else None,
            is_holiday=is_hol,
            is_long_weekend=is_lw,
        ))

    return PreparedBookingData(
        training_dates=df["date"].dt.strftime("%Y-%m-%d").tolist(),
        weekly_records=weekly_records,
        revenue_total=total_revenue,
        snapshot_metadata={
            "avg_lead_time_days": avg_lead_time_days,
            "lead_time_distribution": lead_time_distribution,
            "lead_time_by_year": lead_time_by_year,
            "top_airlines_by_year": top_airlines_by_year,
            "top_routes_by_year": top_routes_by_year,
            "revenue_by_year": revenue_by_year,
            "data_quality_by_year": data_quality_by_year,
            "available_years": available_years,
        },
    )

def dataset_snapshot_fields(
    records: Sequence[WeeklyRecord],
    *,
    avg_lead_time_days: float | None = None,
    lead_time_distribution: list | None = None,
    lead_time_by_year: dict | None = None,
    top_airlines_by_year: dict | None = None,
    top_routes_by_year: dict | None = None,
    revenue_by_year: dict | None = None,
    data_quality_by_year: dict | None = None,
    available_years: list | None = None,
) -> dict[str, Any]:
    """Compute snapshot fields from weekly records in ascending date order."""

    total_weekly_records = len(records)
    total_transaction_count = int(sum(r.booking_value or 0 for r in records))

    revenues = [r.weekly_revenue for r in records if r.weekly_revenue is not None]
    total_revenue = float(sum(revenues)) if revenues else None

    data_start_date = records[0].record_date
    data_end_date   = records[-1].record_date

    booking_values    = [float(r.booking_value or 0) for r in records]
    avg_weekly_bookings = sum(booking_values) / len(booking_values)
    peak_value        = max(booking_values)
    peak_index        = booking_values.index(peak_value)
    peak_week_date    = records[peak_index].record_date
    peak_week_bookings = int(peak_value)

    if total_weekly_records >= 104:
        recent = sum(booking_values[-52:])
        prior  = sum(booking_values[-104:-52])
        growth_rate = round((recent - prior) / prior * 100, 1) if prior > 0 else 0.0
    elif total_weekly_records >= 52:
        recent = sum(booking_values[-26:])
        prior  = sum(booking_values[-52:-26])
        growth_rate = round((recent - prior) / prior * 100, 1) if prior > 0 else 0.0
    else:
        growth_rate = 0.0

    year_map: dict[str, int] = {}
    for r in records:
        key = str(r.record_date.year)
        year_map[key] = year_map.get(key, 0) + int(r.booking_value or 0)
    bookings_by_year = [
        {"year": yr, "bookings": count}
        for yr, count in sorted(year_map.items())
    ]

    month_map: dict[str, int] = {}
    for r in records:
        key = r.record_date.strftime("%Y-%m")
        month_map[key] = month_map.get(key, 0) + int(r.booking_value or 0)
    bookings_by_month = [
        {"month": mo, "bookings": count}
        for mo, count in sorted(month_map.items())
    ]

    revenue_month_map: dict[str, float] = {}
    for r in records:
        if r.weekly_revenue is not None:
            key = r.record_date.strftime("%Y-%m")
            revenue_month_map[key] = round(
                revenue_month_map.get(key, 0.0) + float(r.weekly_revenue), 2
            )
    revenue_by_month = [
        {"month": mo, "revenue": amt}
        for mo, amt in sorted(revenue_month_map.items())
    ]

    holiday_week_count     = sum(1 for r in records if r.is_holiday)
    non_holiday_week_count = total_weekly_records - holiday_week_count

    return {
        "generated_at": datetime.now(MANILA_TIMEZONE),
        "total_transaction_count": total_transaction_count,
        "total_weekly_records": total_weekly_records,
        "total_revenue": total_revenue,
        "data_start_date": data_start_date,
        "data_end_date": data_end_date,
        "span_weeks": total_weekly_records,
        "avg_weekly_bookings": round(avg_weekly_bookings, 2),
        "peak_week_date": peak_week_date,
        "peak_week_bookings": peak_week_bookings,
        "growth_rate": growth_rate,
        "bookings_by_year_json": bookings_by_year,
        "bookings_by_month_json": bookings_by_month,
        "revenue_by_month_json": revenue_by_month,
        "holiday_week_count": holiday_week_count,
        "non_holiday_week_count": non_holiday_week_count,
        "avg_lead_time_days": avg_lead_time_days,
        "lead_time_distribution_json": lead_time_distribution,
        "lead_time_by_year_json": lead_time_by_year,
        "top_airlines_json": top_airlines_by_year,
        "top_routes_json": top_routes_by_year,
        "revenue_by_year_json": revenue_by_year,
        "data_quality_json": data_quality_by_year,
        "available_years_json": available_years,
    }


def business_analytics_view(
    snapshot: Any, year: str, records: Sequence[WeeklyRecord] = ()
) -> dict[str, Any]:
    """Project the legacy Business Analytics response from a dataset snapshot."""
    overall = year == "overall"

    def year_slice(blob: dict | list | None, fallback: Any = None) -> Any:
        if blob is None:
            return fallback
        if isinstance(blob, list):
            return blob if overall else fallback
        return blob.get(year, blob.get("overall", fallback))

    revenue = snapshot.revenue_by_year_json
    scoped_revenue = snapshot.total_revenue if overall or revenue is None else revenue.get(year, snapshot.total_revenue)
    revenue_by_year = []
    if isinstance(revenue, dict):
        revenue_by_year = [
            {"year": key, "revenue": value}
            for key, value in sorted(revenue.items())
            if key != "overall"
        ]
    bookings_by_month = [
        item for item in (snapshot.bookings_by_month_json or [])
        if overall or item.get("month", "").startswith(year)
    ]
    coverage_start = snapshot.data_start_date
    coverage_end = snapshot.data_end_date
    coverage_span = (snapshot.span_weeks or 0) if overall else len(bookings_by_month)
    if not overall and bookings_by_month:
        first_month = bookings_by_month[0]["month"]
        last_month = bookings_by_month[-1]["month"]
        coverage_start = datetime.strptime(first_month, "%Y-%m").replace(tzinfo=coverage_start.tzinfo)
        last_year, last_month_number = map(int, last_month.split("-"))
        coverage_end = datetime(
            last_year, last_month_number, monthrange(last_year, last_month_number)[1],
            tzinfo=coverage_start.tzinfo,
        )

    if overall:
        holiday_weeks = snapshot.holiday_week_count or 0
        non_holiday_weeks = snapshot.non_holiday_week_count or 0
        considered_weeks = snapshot.total_weekly_records or 1
    else:
        year_records = [record for record in records if str(record.record_date.year) == year]
        holiday_weeks = sum(1 for record in year_records if record.is_holiday)
        non_holiday_weeks = len(year_records) - holiday_weeks
        considered_weeks = len(year_records) or 1

    lead_time_by_year = getattr(snapshot, "lead_time_by_year_json", None)
    if overall or not lead_time_by_year:
        avg_lead = snapshot.avg_lead_time_days
        lead_distribution = snapshot.lead_time_distribution_json or []
    else:
        lead_time = lead_time_by_year.get(year) or {}
        avg_lead = lead_time.get("avg")
        lead_distribution = lead_time.get("distribution") or []

    return {
        "generated_at": snapshot.generated_at.isoformat(),
        "total_transaction_count": snapshot.total_transaction_count or 0,
        "total_weekly_records": snapshot.total_weekly_records or 0,
        "total_revenue": scoped_revenue,
        "avg_weekly_bookings": snapshot.avg_weekly_bookings or 0.0,
        "peak_week_date": snapshot.peak_week_date.isoformat(),
        "peak_week_bookings": snapshot.peak_week_bookings or 0,
        "growth_rate": snapshot.growth_rate or 0.0,
        "date_coverage": {
            "start_date": coverage_start.isoformat(),
            "end_date": coverage_end.isoformat(),
            "span_weeks": coverage_span,
        },
        "bookings_by_year": [
            item for item in (snapshot.bookings_by_year_json or [])
            if overall or item.get("year") == year
        ],
        "bookings_by_month": bookings_by_month,
        "revenue_by_month": [
            item for item in (snapshot.revenue_by_month_json or [])
            if overall or item.get("month", "").startswith(year)
        ],
        "holiday_breakdown": {
            "holiday_weeks": holiday_weeks,
            "non_holiday_weeks": non_holiday_weeks,
            "holiday_pct": round(holiday_weeks / considered_weeks * 100, 1),
        },
        "avg_lead_time_days": avg_lead,
        "lead_time_distribution": lead_distribution,
        "top_airlines": year_slice(snapshot.top_airlines_json, []),
        "top_routes": year_slice(snapshot.top_routes_json, []),
        "revenue_by_year": revenue_by_year,
        "data_quality": year_slice(snapshot.data_quality_json) or None,
        "available_years": snapshot.available_years_json or [],
    }


def prepare_browser_bookings(file_bytes: bytes, *, cutoff: pd.Timestamp | None = None) -> BrowserBookings:
    """Return the same booking analytics and training dates entirely in the browser."""
    try:
        prepared = prepare_booking_data(file_bytes, cutoff=cutoff, browser_safe_labels=True)
    except (pd.errors.EmptyDataError, pd.errors.ParserError, UnicodeError):
        raise BookingCSVError("Use a valid UTF-8 booking CSV.") from None

    snapshot = SimpleNamespace(**dataset_snapshot_fields(prepared.weekly_records, **prepared.snapshot_metadata))
    views = {
        year: business_analytics_view(snapshot, year, prepared.weekly_records)
        for year in ("overall", *snapshot.available_years_json)
    }
    return BrowserBookings(prepared.training_dates, prepared.weekly_records, views, max(prepared.training_dates))
