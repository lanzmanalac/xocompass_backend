import pandas as pd
import io
from datetime import timedelta
from sqlalchemy.orm import Session
from domain.models import TrainingDataLog
from core.exogenous import PHHolidayEngine
from zoneinfo import ZoneInfo
from services.analytics_service import compute_and_persist_dataset_snapshot
import uuid

# ── NEW: Private helper — computes top-N aggregation both globally
# and per calendar year from a raw pandas Series.
#
# ISO 25010 → Maintainability → Modularity:
#   This helper is the single point of truth for the "top-N per year"
#   pattern. Both top_airlines and top_routes use it. Adding a new
#   year-aware metric in the future is one function call.
#
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

# ── NEW: Private helper — computes the Data Quality report.
#
# ISO 25010 → Reliability → Maturity:
#   Data quality is computed once at ingestion time and persisted.
#   The GET endpoint never touches the raw CSV again — it just reads
#   the pre-computed report from DatasetSnapshot.
#
# ISO 25010 → Maintainability → Analysability:
#   Each quality dimension is a named field, not a magic number.
#   Adding a new quality check is one new key in the returned dict.
#
# Returns a dict shaped like:
#   {
#     "overall": { <quality fields> },
#     "2013":    { <quality fields> },
#     ...
#   }
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

        # Duplicate detection: a row is a duplicate if booking date
        # + airline + route all match a prior row. We use a broad
        # subset so we catch true duplicates without over-flagging.
        dup_subset = [c for c in ["date", "_airline_clean", "_route_clean"] if c in slice_df.columns]
        duplicate_rows = int(slice_df.duplicated(subset=dup_subset).sum()) if dup_subset else 0

        missing_airline = int(slice_df["_airline_clean"].isna().sum()) if "_airline_clean" in slice_df.columns else total
        missing_route   = int(slice_df["_route_clean"].isna().sum())   if "_route_clean"   in slice_df.columns else total

        if "travel_date" in slice_df.columns:
            missing_travel_date = int(slice_df["travel_date"].isna().sum())
            # Invalid = travel date is BEFORE booking date (negative lead time)
            # These were already flagged during lead-time computation.
            invalid_travel_date = int(
                ((slice_df["travel_date"] - slice_df["date"]).dt.days < 0)
                .sum()
            ) if "travel_date" in slice_df.columns else 0
        else:
            missing_travel_date = total
            invalid_travel_date = 0

        missing_revenue = int(slice_df["_revenue_cleaned"].isna().sum()) if "_revenue_cleaned" in slice_df.columns else total

        # Quality score: % of rows that are clean on ALL dimensions.
        # A row is "dirty" if it hits ANY quality issue.
        # This is intentionally strict — it gives you a conservative
        # defensible number for the thesis.
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


def ingest_csv(file_bytes: bytes, db: Session) -> dict:
    ph_tz = ZoneInfo("Asia/Manila")

    ingestion_batch_id = str(uuid.uuid4())


    db.query(TrainingDataLog).delete()
    db.commit()

    df = pd.read_csv(io.BytesIO(file_bytes))

    # ── DEBUG: print all column names as Python sees them ──
    print("📋 CSV columns detected on upload:")
    for c in df.columns:
        print(f"   repr: {repr(c)}")


    # 1. Find the date column dynamically
    date_col = next((c for c in df.columns if 'date' in c.lower()), None)
    if not date_col:
        raise ValueError("No date column found. Column must contain 'date'.")
    
    # 1.a. Find Net Amount column (normalized match)
    normalized_cols = {c: c.strip().lower() for c in df.columns}
    revenue_col = next(
        (original for original, normalized in normalized_cols.items()
         if "net amount" in normalized),
        None,
    )
    print(f"   Revenue column detected: {repr(revenue_col)}")


    df['date'] = pd.to_datetime(df[date_col], format='mixed', dayfirst=False, errors='coerce')
    df = df.dropna(subset=['date'])

    # ── Detect Travel Date column ─────────────────────────────────────────
    travel_col = next(
        (c for c in df.columns if 'travel' in c.lower() and 'date' in c.lower()),
        None,
    )
    print(f"   Travel date column detected: {repr(travel_col)}")

    # ── Detect Airline Code column ────────────────────────────────────────
    airline_col = next(
        (c for c in df.columns if 'airline' in c.lower()),
        None,
    )
    print(f"   Airline column detected: {repr(airline_col)}")


    # ── NEW 1.d. Detect Route column ──────────────────────────────────────
    # Strategy 1: Look for a single column named 'route' or 'sector'
    route_col = next(
        (c for c in df.columns
         if any(kw in c.lower() for kw in ['route', 'sector'])),
        None,
    )
    # Strategy 2: Look for separate origin + destination columns and
    # concatenate them as "ORIGIN-DESTINATION" (e.g., "MNL-NRT").
    origin_col = next(
        (c for c in df.columns if any(kw in c.lower() for kw in ['origin', 'from', 'departure'])),
        None,
    )
    dest_col = next(
        (c for c in df.columns if any(kw in c.lower() for kw in ['destination', 'dest', 'to', 'arrival'])),
        None,
    )
    if route_col:
        df['_route_clean'] = df[route_col].astype(str).str.strip().str.upper()
        print(f"   Route column detected (single): {repr(route_col)}")
    elif origin_col and dest_col:
        df['_route_clean'] = (
            df[origin_col].astype(str).str.strip().str.upper()
            + "-"
            + df[dest_col].astype(str).str.strip().str.upper()
        )
        # Treat "NAN-NAN" as missing
        df.loc[df['_route_clean'].str.contains('NAN'), '_route_clean'] = None
        print(f"   Route column constructed from: {repr(origin_col)} + {repr(dest_col)}")
    else:
        df['_route_clean'] = None
        print("   ⚠️  No route column detected — top routes will not be computed.")

    # ── NEW 1.e. Normalize airline column for quality checks ──────────────
    if airline_col:
        df['_airline_clean'] = (
            df[airline_col]
            .astype(str)
            .str.strip()
            .replace({'nan': None, 'NaN': None, '': None})
        )
    else:
        df['_airline_clean'] = None

    # ── Compute Lead Time (raw transaction level) ─────────────────────────
    # Lead time = how many days BEFORE travel date the customer booked.
    # Computed here from raw rows before weekly aggregation loses this info.
    avg_lead_time_days = None
    lead_time_distribution = None

    if travel_col:
        df['travel_date'] = pd.to_datetime(
            df[travel_col], format='mixed', dayfirst=False, errors='coerce'
        )
        df['lead_time'] = (df['travel_date'] - df['date']).dt.days

        # Only keep positive lead times (negative = data error)
        valid_lead = df['lead_time'].dropna()
        valid_lead = valid_lead[valid_lead >= 0]

        if len(valid_lead) > 0:
            avg_lead_time_days = round(float(valid_lead.mean()), 1)

            # Pre-bucket into histogram bins — server-side so frontend
            # just renders bars. ISO 25010 → Performance Efficiency.
            bins = [
                (0,   7,   "0-7 days"),
                (8,   14,  "8-14 days"),
                (15,  30,  "15-30 days"),
                (31,  60,  "31-60 days"),
                (61,  90,  "61-90 days"),
                (91,  float('inf'), "90+ days"),
            ]
            lead_time_distribution = []
            for low, high, label in bins:
                count = int(((valid_lead >= low) & (valid_lead <= high)).sum())
                lead_time_distribution.append({
                    "bucket": label,
                    "count": count,
                })

            print(f"   ⏱️  Avg lead time: {avg_lead_time_days} days "
                  f"({len(valid_lead):,} valid transactions)")
        else:
            print("   ⚠️  No valid lead time data found.")
    else:
        print("   ⚠️  No travel date column — lead time will not be computed.")

    # ── 3. Revenue computation ─────────────────────────────────────────────
    if revenue_col:
        raw_rev     = df[revenue_col].astype(str)
        is_negative = raw_rev.str.strip().str.startswith("(")
        cleaned     = raw_rev.str.replace(r"[^\d.]", "", regex=True)
        numeric_rev = pd.to_numeric(cleaned, errors="coerce")
        numeric_rev = numeric_rev.where(~is_negative, other=-numeric_rev)
        df["_revenue_cleaned"] = numeric_rev

        # ── NEW: Revenue by year (for the bar graph KPI card) ─────────────
        # Shape: {"overall": 45000000.0, "2013": 3200000.0, "2014": 4100000.0, ...}
        df["_year"] = pd.to_datetime(df["Generation Date"], format='mixed').dt.year
        
        revenue_by_year = {}
        for year, grp in df.groupby("_year"):
            revenue_by_year[str(int(year))] = round(float(grp["_revenue_cleaned"].sum()), 2)
        print(f"   💰 Revenue by year computed: {list(revenue_by_year.keys())}")
    else:
        df["_revenue_cleaned"] = None
        revenue_by_year = None  # ── NEW
        print("   ⚠️  No Net Amount column — revenue will not be computed.")

    # ── NEW 4. Top airlines — upgraded to per-year ────────────────────────
    # MODIFIED: was a flat list; now a year-keyed dict via the helper.
    # The "overall" key is the exact same computation as the old flat list,
    # so existing snapshot rows are conceptually equivalent.
    if airline_col:
        top_airlines_by_year = _compute_top_n_by_year(
            series=df['_airline_clean'],
            year_series=df['_year'],
            key_name="airline_code",
            top_n=7,
        )
        print(f"   ✈️  Top airlines (overall): {[a['airline_code'] for a in top_airlines_by_year['overall']]}")
    else:
        top_airlines_by_year = None
        print("   ⚠️  No airline column — top airlines will not be computed.")

    # ── NEW 5. Top routes — per year ──────────────────────────────────────
    route_series = df['_route_clean'] if '_route_clean' in df.columns else pd.Series(dtype=str)
    has_routes = route_series.notna().any()

    if has_routes:
        top_routes_by_year = _compute_top_n_by_year(
            series=df['_route_clean'],
            year_series=df['_year'],
            key_name="route",
            top_n=10,
        )
        print(f"   🗺️  Top routes (overall): {[r['route'] for r in top_routes_by_year['overall'][:3]]}")
    else:
        top_routes_by_year = None
        print("   ⚠️  No route column — top routes will not be computed.")

    # ── NEW 6. Data Quality report ─────────────────────────────────────────
    data_quality_by_year = _compute_data_quality(df, df['_year'])
    print(f"   🔍 Data quality score (overall): {data_quality_by_year['overall']['quality_score_pct']}%")

    # ── NEW 7. Available years list ────────────────────────────────────────
    # Stored on the snapshot so the frontend dropdown knows which years
    # to offer without having to derive it from the data arrays.
    available_years = sorted([str(int(y)) for y in df['_year'].dropna().unique()])
    print(f"   📅 Available years: {available_years}")

    # ── 8. Group into Weekly Buckets ──────────────────────────────────────
    df["week"] = df["date"].dt.to_period("W-MON").dt.start_time
    weekly_counts = df.groupby("week").size().reset_index(name="booking_value")

    if revenue_col:
        weekly_revenue = df.groupby("week")["_revenue_cleaned"].sum().reset_index(name="weekly_revenue")
        weekly = weekly_counts.merge(weekly_revenue, on="week", how="left")
        total_revenue = float(df["_revenue_cleaned"].sum())
        print(f"   💰 Total revenue: ₱{total_revenue:,.2f}")
    else:
        weekly = weekly_counts.copy()
        weekly["weekly_revenue"] = None
        total_revenue = None

    # ── 9. Deduplication ───────────────────────────────────────────────────
    existing_dates = {
        row.record_date.replace(tzinfo=None)
        for row in db.query(TrainingDataLog.record_date).all()
    }
    new_rows = weekly[~weekly["week"].isin(existing_dates)]

    if new_rows.empty:
        return {"status": "skipped", "message": "No new records.", "new_records": 0}

    # ── 10. Generate Philippine Holidays ───────────────────────────────────
    holidays_df = PHHolidayEngine().generate(
        new_rows["week"].min() - timedelta(days=7),
        new_rows["week"].max() + timedelta(days=7)
    )

    db_rows = []
    for _, row in new_rows.iterrows():
        dt_naive = row["week"]
        dt = dt_naive.replace(tzinfo=ph_tz)
        wk_end = dt_naive + timedelta(days=6)

        sl = holidays_df.reindex(
            pd.date_range(dt_naive, wk_end, freq="D")
        ).fillna(0)

        is_hol = bool(sl["is_holiday"].max() > 0)
        is_lw  = bool(sl["is_long_weekend"].max() > 0)

        db_rows.append(TrainingDataLog(
            record_date=dt,
            booking_value=float(row["booking_value"]),
            is_holiday=is_hol,
            weather_indicator=0.0,
            weekly_revenue=float(row["weekly_revenue"]) if row.get("weekly_revenue") is not None else None,
            additional_exog_json={"is_long_weekend": int(is_lw)},
            ingestion_batch_id=ingestion_batch_id,
        ))

    # ── 11. Persist — atomic with snapshot ────────────────────────────────
    db.add_all(db_rows)
    db.flush()

    # Pass ALL new computed data to the snapshot service.
    # The snapshot service stages the write; this caller owns db.commit().
    compute_and_persist_dataset_snapshot(
        db,
        ingestion_batch_id,
        avg_lead_time_days=avg_lead_time_days,
        lead_time_distribution=lead_time_distribution,
        # ── MODIFIED: passing year-keyed dicts instead of flat lists ──
        top_airlines_by_year=top_airlines_by_year,      # ── NEW param name
        top_routes_by_year=top_routes_by_year,          # ── NEW
        revenue_by_year=revenue_by_year,                # ── NEW
        data_quality_by_year=data_quality_by_year,      # ── NEW
        available_years=available_years,                # ── NEW
    )

    db.commit()

    print(f"   ✅ Ingested {len(db_rows)} weeks.")

    return {
        "status": "success",
        "message": f"Ingested {len(db_rows)} new weekly records.",
        "new_records": len(db_rows),
        "revenue_total": total_revenue,
        "ingestion_batch_id": ingestion_batch_id,
        "avg_lead_time_days": avg_lead_time_days,
    }
