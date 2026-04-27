import pandas as pd
import io
from datetime import timedelta
from sqlalchemy.orm import Session
from domain.models import TrainingDataLog
from core.exogenous import PHHolidayEngine
from zoneinfo import ZoneInfo
from services.analytics_service import compute_and_persist_dataset_snapshot
import uuid


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

    # ── Compute Top Airlines (raw transaction level) ──────────────────────
    top_airlines = None

    if airline_col:
        airline_counts = (
            df[airline_col]
            .dropna()
            .astype(str)
            .str.strip()
            .value_counts()
            .head(7)
        )
        total_with_airline = int(airline_counts.sum())
        top_airlines = [
            {
                "airline_code": code,
                "count": int(count),
                "pct": round(float(count) / total_with_airline * 100, 1)
                if total_with_airline > 0 else 0.0,
            }
            for code, count in airline_counts.items()
        ]
        print(f"   ✈️  Top airlines: {[a['airline_code'] for a in top_airlines]}")
    else:
        print("   ⚠️  No airline column — top airlines will not be computed.")

    # 2. Group into Weekly Buckets
    df["week"] = df["date"].dt.to_period("W-MON").dt.start_time

    weekly_counts = df.groupby("week").size().reset_index(name="booking_value")
    
    if revenue_col:
        raw_rev     = df[revenue_col].astype(str)
        is_negative = raw_rev.str.strip().str.startswith("(")
        cleaned     = raw_rev.str.replace(r"[^\d.]", "", regex=True)
        numeric_rev = pd.to_numeric(cleaned, errors="coerce").fillna(0.0)
        numeric_rev = numeric_rev.where(~is_negative, other=-numeric_rev)
        df["_revenue_cleaned"] = numeric_rev

        weekly_revenue = df.groupby("week")["_revenue_cleaned"].sum().reset_index(name="weekly_revenue")
        weekly = weekly_counts.merge(weekly_revenue, on="week", how="left")
        total_revenue = float(weekly["weekly_revenue"].sum())   # ← inside if block
        print(f"   💰 Total revenue: ₱{total_revenue:,.2f}")

    else:
        weekly = weekly_counts.copy()
        weekly["weekly_revenue"] = None
        total_revenue = None   # ← inside else block
        print("   ⚠️  No Net Amount column — revenue will use proxy at forecast time.")
    # 3. Deduplication (prevents double-counting if the client uploads the same file twice)
    existing_dates = {
        row.record_date.replace(tzinfo=None)
        for row in db.query(TrainingDataLog.record_date).all()
    }
    new_rows = weekly[~weekly["week"].isin(existing_dates)]

    if new_rows.empty:
        return {"status": "skipped", "message": "No new records.", "new_records": 0}

    # 4. Generate Real Philippine Holidays
    holidays_df = PHHolidayEngine().generate(
        new_rows["week"].min() - timedelta(days=7),
        new_rows["week"].max() + timedelta(days=7)
    )

    db_rows = []
    for _, row in new_rows.iterrows():
        dt_naive = row["week"]
        dt = dt_naive.replace(tzinfo=ph_tz)
        wk_end = dt_naive + timedelta(days=6)
        
        # Check if any day in this week is a holiday or long weekend
        sl = holidays_df.reindex(
            pd.date_range(dt_naive, wk_end, freq="D")
        ).fillna(0)
        
        is_hol = bool(sl["is_holiday"].max() > 0)
        is_lw  = bool(sl["is_long_weekend"].max() > 0)

        # 5. Create the ORM Object
        db_rows.append(TrainingDataLog(
            record_date=dt,
            booking_value=float(row["booking_value"]),
            is_holiday=is_hol,
            weather_indicator=0.0,
            weekly_revenue=float(row["weekly_revenue"]) if row.get("weekly_revenue") is not None else None,
            additional_exog_json={"is_long_weekend": int(is_lw)},
            ingestion_batch_id=ingestion_batch_id,
        ))

    # 6. Save to Neon
    db.add_all(db_rows)
    db.flush()
    # atomically rebuild the business analytics snapshot alongside the data write
    compute_and_persist_dataset_snapshot(
        db,
        ingestion_batch_id,
        avg_lead_time_days=avg_lead_time_days,
        lead_time_distribution=lead_time_distribution,
        top_airlines=top_airlines,
    )

    db.commit()
    
    total_rev = float(weekly.loc[weekly["week"].isin(new_rows["week"]), "weekly_revenue"].sum())
    print(f"   ✅ Ingested {len(db_rows)} weeks. Total revenue stored: ₱{total_rev:,.2f}")

    return {
        "status": "success",
        "message": f"Ingested {len(db_rows)} new weekly records.",
        "new_records": len(db_rows),
        "revenue_total": total_revenue,
        "ingestion_batch_id": ingestion_batch_id,
        "avg_lead_time_days": avg_lead_time_days,
    }
