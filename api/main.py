from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

# ── IMPORTING FROM YOUR OTHER LAYERS ──
# (You will define these functions in their respective folders)
from services.forecast_service import generate_forecast_payload
from repository.model_repository import fetch_all_models_metadata, fetch_dashboard_stats

app = FastAPI(title="XoCompass API", version="10.1")

# ════════════════════════════════════════════════════════════════════════════
# 0. MIDDLEWARE & ROUTING CONFIGURATION
# ════════════════════════════════════════════════════════════════════════════

# CORS setup for the Polyrepo architecture
origins = [
    "http://localhost:3000",        # Frontend Dev Local
    "http://localhost:5173",        # Vite Local
    "https://xocompass.vercel.app"  # Future Deployed UI
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def home():
    """Automatically redirect base URL to the Swagger UI dashboard."""
    return RedirectResponse(url="/docs")

# ── REQUEST SCHEMAS ──
class ForecastRequest(BaseModel):
    model_id: Optional[int] = None  # If null, grabs the baseline/latest model


# ════════════════════════════════════════════════════════════════════════════
# 1. PRESENTATION LAYER ENDPOINTS (The "Traffic Cops")
# ════════════════════════════════════════════════════════════════════════════

@app.get("/api/models")
def get_model_registry():
    """Presentation layer asking the Repository layer for the dropdown options."""
    try:
        models = fetch_all_models_metadata()
        return {"available_models": models}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/dashboard-stats/{model_id}")
def get_dashboard_stats(model_id: int):
    """Presentation layer asking the Repository for the business metrics snapshot."""
    try:
        stats = fetch_dashboard_stats(model_id)
        if not stats:
            raise HTTPException(status_code=404, detail="Model snapshot not found.")
        return stats
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/forecast")
def generate_forecast(request: ForecastRequest):
    """Presentation layer asking the Service layer to run the ML inference."""
    try:
        # The Service layer handles joblib deserialization and statsmodels logic
        payload = generate_forecast_payload(request.model_id)
        return payload
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/append_data")
def update_model_state():
    """Placeholder endpoint for future incremental learning updates."""
    return {"status": "success", "message": "New data appended without GridSearch retraining."}