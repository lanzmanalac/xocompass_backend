from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
# Added engine and Base to the imports
from repository.model_repository import SessionLocal, engine
from domain.models import Base, SarimaxModel, TrainingDataLog, ModelDiagnostic, ForecastCache

def seed_data():
    # 0. THE FIX: Force SQLAlchemy to build the tables if they are missing
    print("🏗️ Ensuring database tables exist...")
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    ph_tz = ZoneInfo("Asia/Manila")
    now_ph = datetime.now(ph_tz)

    print("🌱 Seeding mock data for XoCompass...")

    mock_diagnostic = ModelDiagnostic(
        model_id=1,
        residuals_json=[
            {"fitted": v, "residual": round((v * 0.05) - 2.5, 2)}
            for v in range(80, 200, 4)
        ],
        acf_values_json=[
            round(0.9 * (0.85 ** i), 3) for i in range(13)
        ],
        pacf_values_json=[
            round(0.9 * (0.6 ** i), 3) for i in range(13)
        ],
        # ADF
        adf_stat=-4.23,
        adf_pvalue=0.001,
        adf_conclusion="Series is stationary after differencing.",
        # Ljung-Box
        ljungbox_stat=18.7,
        ljungbox_pvalue=0.29,
        ljungbox_conclusion="Residuals show no significant autocorrelation.",
        # Jarque-Bera
        jarquebera_stat=1.87,
        jarquebera_pvalue=0.39,
        jarquebera_conclusion="Residuals are approximately normal around zero.",

        correlation_json=[ 
            {"variable": "holiday_lead_2", "correlation": 0.61},
            {"variable": "is_long_weekend",  "correlation": 0.44},
            {"variable": "typhoon_msw",      "correlation": -0.38},
            {"variable": "holiday_intensity","correlation": 0.52},
          ],

    )

    # 1. Mock Model Registry (The Brain)
    # Update SarimaxModel seed block
    mock_model = SarimaxModel(
        model_name="XoCompass SARIMAX v1",
        model_path="data/models/mock_sarimax.joblib",
        is_active=True,
        pipeline_ver="v10.1",
        p=2, d=1, q=2,
        seasonal_p=1, seasonal_d=1, seasonal_q=1,
        exog_features_json=["holiday_lead_2", "is_long_weekend"],
        aic_score=204.3,
        bic_score=218.1,
        mae=9.8,
        rmse=12.4,
        mape=0.089,
        wmape=0.01232,        # matches frontend's 4.2% display
        train_start_date=datetime(2013, 1, 7, tzinfo=ZoneInfo("Asia/Manila")),
        train_end_date=datetime(2025, 12, 29, tzinfo=ZoneInfo("Asia/Manila")),
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
    # Write KPI columns directly onto the model row (no separate snapshot table)
    mock_model.total_records = 104
    mock_model.data_quality_pct = 98.5
    mock_model.revenue_total = 2450000.00
    mock_model.growth_rate = 12.5
    mock_model.expected_bookings = 450
    mock_model.peak_travel_period = "April - May 2026"
    mock_model.yearly_bookings_json = [
        {"year": "2023", "bookings": 312},
        {"year": "2024", "bookings": 401},
        {"year": "2025", "bookings": 450},
    ]
    db.commit()

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

    MOCK_RISK_FLAGS = ["HIGH", "MEDIUM", "LOW", "LOW"]

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
            risk_flag=MOCK_RISK_FLAGS[i],  # ← NEW
        )
        db.add(cache)

    db.commit()
    db.close()
    print("✅ Mock data successfully seeded! The database is ready for the API.")

if __name__ == "__main__":
    seed_data()