from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
# Added engine and Base to the imports
from repository.model_repository import SessionLocal, engine
from domain.models import Base, SarimaxModel, TrainingDataLog, ForecastSnapshot, ModelDiagnostic, ForecastCache

def seed_data():
    # 0. THE FIX: Force SQLAlchemy to build the tables if they are missing
    print("🏗️ Ensuring database tables exist...")
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    ph_tz = ZoneInfo("Asia/Manila")
    now_ph = datetime.now(ph_tz)

    print("🌱 Seeding mock data for XoCompass...")

    # 1. Mock Model Registry (The Brain)
    mock_model = SarimaxModel(
        model_name="Production_SARIMAX_MVP",
        model_path="data/models/mock_model_v1.joblib",
        is_active=True,
        pipeline_ver="v10.1",
        p=1, d=1, q=1,
        seasonal_p=0, seasonal_d=1, seasonal_q=1,
        exog_features_json=["is_holiday", "typhoon_msw"],
        aic_score=1250.4, bic_score=1265.1,
        mae=15.2, rmse=22.4, mape=0.08,
        train_start_date=now_ph - timedelta(days=365),
        train_end_date=now_ph - timedelta(days=7),
        created_at=now_ph
    )
    db.add(mock_model)
    db.commit()
    db.refresh(mock_model)

    # 2. Mock Training Ledger (Page 3 Bookings History)
    for i in range(10):
        past_date = now_ph - timedelta(weeks=10-i)
        log = TrainingDataLog(
            record_date=past_date,
            booking_value=120.0 + (i * 5),
            is_holiday=(i % 4 == 0),
            weather_indicator=0.0,
            ingested_at=now_ph
        )
        db.add(log)

    # 3. Mock Snapshot (Page 1 KPIs)
    snapshot = ForecastSnapshot(
        model_id=mock_model.id,
        generated_at=now_ph,
        total_records=104,
        data_quality_pct=98.5,
        revenue_total=2450000.00,
        growth_rate=12.5,
        expected_bookings=450,
        peak_travel_period="April - May 2026"
    )
    db.add(snapshot)

    # 4. Mock Diagnostics (Page 2 MLOps Charts)
    diag = ModelDiagnostic(
        model_id=mock_model.id,
        residuals_json=[{"fitted": 100, "residual": -2}, {"fitted": 110, "residual": 5}],
        acf_values_json=[1.0, 0.4, 0.1, -0.05, -0.02],
        pacf_values_json=[1.0, 0.3, 0.05, -0.01, 0.0],
        adf_stat=-3.45, adf_pvalue=0.008,
        ljungbox_pvalue=0.45, jarquebera_pvalue=0.32
    )
    db.add(diag)

    # 5. Mock Forecast Cache (Page 1 Actual vs Predicted Graph)
    for i in range(4):
        future_date = now_ph + timedelta(weeks=i+1)
        cache = ForecastCache(
            model_id=mock_model.id,
            forecast_date=future_date,
            predicted=150.0 + (i * 10),
            lower_bound=130.0 + (i * 10),
            upper_bound=170.0 + (i * 10),
            generated_at=now_ph,
            periods_ahead=i+1
        )
        db.add(cache)

    db.commit()
    db.close()
    print("✅ Mock data successfully seeded! The database is ready for the API.")

if __name__ == "__main__":
    seed_data()