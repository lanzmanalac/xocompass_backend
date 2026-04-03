import pandas as pd
import numpy as np

# ── IMPORT FROM THE REPOSITORY LAYER ──
from repository.model_repository import fetch_model_binary

# (Optional: Later, you will import your PHHolidayEngine here to generate real exogenous data)
# from core.exogenous import PHHolidayEngine 

def generate_forecast_payload(model_id: int | None = None) -> dict:
    """
    BUSINESS LOGIC: Fetches the model, calculates future exogenous variables, 
    runs the SARIMAX forecast, and formats the output for the frontend.
    """
    
    # 1. Ask the Repository layer to fetch and un-shrink-wrap the joblib model
    sm_model, metadata = fetch_model_binary(model_id)
    
    # 2. Define the forecast horizon
    steps = 16
    
    # 3. Generate Future Dates
    # In a fully dynamic system, this start date would be: metadata["train_end_date"] + 1 week
    future_idx = pd.date_range("2026-01-05", periods=steps, freq="W-MON")
    
    # 4. Generate Future Exogenous Variables (The "Context")
    # ⚠️ PLACEHOLDER: Right now we pass zeroes so the math doesn't crash.
    # Eventually, you will run your PHHolidayEngine() here to generate real holiday flags!
    forecast_exog = pd.DataFrame({
        "holiday_lead_1": [0] * steps, 
        "is_long_weekend": [0] * steps
    }, index=future_idx)

    # 5. Execute the Inference (The actual ML Math)
    fobj = sm_model.get_forecast(steps=steps, exog=forecast_exog)
    
    # 6. Apply FIX 2 (Direct booking counts, floored at 0)
    pred  = fobj.predicted_mean.clip(lower=0)
    ci    = fobj.conf_int(alpha=0.05)
    ci_lo = ci.iloc[:, 0].clip(lower=0)
    ci_hi = ci.iloc[:, 1].clip(lower=0)
    
    # 7. Package the data into the exact JSON schema the Frontend needs
    forecast_array = []
    for i, dt in enumerate(future_idx):
        forecast_array.append({
            "week_start": dt.strftime("%Y-%m-%d"),
            "forecast_bookings": int(np.round(pred.iloc[i])),
            "confidence_lower_95": int(np.round(ci_lo.iloc[i])),
            "confidence_upper_95": int(np.round(ci_hi.iloc[i])),
            # Frontend UI tags
            "holiday_lead_level": int(forecast_exog["holiday_lead_1"].iloc[i]),
            "is_long_weekend": int(forecast_exog["is_long_weekend"].iloc[i]),
            "typhoon_climate_flag": 0  # Placeholder
        })

    # 8. Return the final payload back up to the API Layer
    return {
        "metadata": metadata,
        "forecast_data": forecast_array
    }