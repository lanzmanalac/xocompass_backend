import pandas as pd
import io
from datetime import timedelta
from sqlalchemy.orm import Session
from domain.models import TrainingDataLog
from core.exogenous import PHHolidayEngine
from zoneinfo import ZoneInfo

def ingest_csv(file_bytes: bytes, db: Session) -> dict:
    ph_tz = ZoneInfo("Asia/Manila")
    df = pd.read_csv(io.BytesIO(file_bytes))

    # 1. Find the date column dynamically
    date_col = next((c for c in df.columns if 'date' in c.lower()), None)
    if not date_col:
        raise ValueError("No date column found. Column must contain 'date'.")

    df['date'] = pd.to_datetime(df[date_col], errors='coerce')
    df = df.dropna(subset=['date'])

    # 2. Group into Weekly Buckets
    df["week"] = df["date"].dt.to_period("W-MON").dt.start_time
    weekly = df.groupby("week").size().reset_index(name="booking_value")

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
            additional_exog_json={"is_long_weekend": int(is_lw)},
        ))

    # 6. Save to Neon
    db.add_all(db_rows)
    db.commit()
    
    return {
        "status": "success",
        "message": f"Ingested {len(db_rows)} new weekly records.",
        "new_records": len(db_rows)
    }