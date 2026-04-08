import pandas as pd
import io
from sqlalchemy.orm import Session
from domain.models import TrainingDataLog
from core.feature_contract import FeatureContract
from zoneinfo import ZoneInfo

def ingest_csv(file_bytes: bytes, db: Session) -> dict:
    # 1. Parse CSV
    df = pd.read_csv(io.BytesIO(file_bytes))
    
    # Identify the date column
    date_col = next((c for c in df.columns if 'date' in c.lower()), None)
    if not date_col:
        raise ValueError("No date column found in CSV. Please ensure the column has 'date' in the name.")
        
    df['date'] = pd.to_datetime(df[date_col], errors='coerce')
    df = df.dropna(subset=['date'])
    
    # 2. Group into Weekly (W-MON) buckets
    df["week"] = df["date"].dt.to_period("W-MON").dt.start_time
    weekly = df.groupby("week").size().reset_index(name="booking_value")
    
    # 3. Prevent Duplicates
    existing_dates = {row.record_date.replace(tzinfo=None) for row in db.query(TrainingDataLog.record_date).all()}
    new_rows = weekly[~weekly["week"].isin(existing_dates)]
    
    if new_rows.empty:
        return {"status": "skipped", "message": "No new records. Data is up to date.", "new_records": 0}
        
    # 4. Use the Feature Contract (Architect Way)
    contract = FeatureContract()
    exog_df = contract.build_exog(pd.DatetimeIndex(new_rows["week"]))
    
    # 5. Persist to DB
    ph_tz = ZoneInfo("Asia/Manila")
    db_rows = []
    
    for _, row in new_rows.iterrows():
        dt = row["week"].replace(tzinfo=ph_tz)
        
        # Safely extract exog_df data (will be {} for now)
        exog_dict = exog_df.loc[row["week"]].to_dict() if not exog_df.empty else {}
        
        log_entry = TrainingDataLog(
            record_date=dt,
            booking_value=float(row["booking_value"]),
            is_holiday=False, # Phase 1 Placeholder
            weather_indicator=0.0, # Phase 1 Placeholder
            additional_exog_json=exog_dict  # Safely inserts {}
        )
        db_rows.append(log_entry)
        
    db.add_all(db_rows)
    db.commit()
    
    return {"status": "success", "message": f"Successfully ingested {len(db_rows)} weeks.", "new_records": len(db_rows)}