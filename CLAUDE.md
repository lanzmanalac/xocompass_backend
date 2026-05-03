# XoCompass — AI Context File

## Project Summary
XoCompass is a context-aware tourism demand forecasting backend designed for an MSME travel agency thesis project. It trains and serves an interpretable SARIMAX model using weekly-aggregated booking data and exogenous signals (Philippine holidays, typhoon wind-speed events). The backend exposes a FastAPI API for model registry access, dashboard statistics, and forecasting capabilities.

## Tech Stack
* **Language:** Python 3.10+
* **API:** FastAPI (`fastapi`), Uvicorn (`uvicorn[standard]`)
* **Data / ETL:** Pandas, NumPy
* **Time Series / Econometrics:** Statsmodels (SARIMAX, ADF, STL), pmdarima (auto_arima grid search)
* **Metrics:** xskillscore, xarray
* **Visualization (Pipeline Only):** Matplotlib, Seaborn
* **Persistence:** SQLite + SQLAlchemy (`sqlalchemy`), Joblib blobs (`joblib`)
* **HTTP:** Requests (present in dependencies; not yet used in core API path)

## Folder Structure
```text
xocompass_backend/
├── api/
│   └── main.py                     # FastAPI app + CORS + endpoints
├── services/
│   ├── forecast_service.py         # Inference service (loads model, forecasts, formats JSON)
│   └── pipeline/                   # 6-step offline training/evaluation pipeline
│       ├── orchestrator.py         # Runs steps 1→6 sequentially
│       ├── step1_ingestion.py      # Aggregation, feature build, optimal holiday lead search
│       ├── step2_correlations.py   # Correlation matrix + ACF/PACF
│       ├── step3_stationarity.py   # ADF-driven differencing + auto_arima search
│       ├── step4_decomposition.py  # STL decomposition
│       ├── step5_training.py       # Fit SARIMAX, residual diagnostics, coefficient interpretation
│       └── step6_evaluation.py     # Metrics (WMAPE/MAE/RMSE) + future forecast payload
├── repository/
│   └── model_repository.py         # SQLAlchemy model, fetch functions, joblib deserialization
├── domain/
│   └── models.py                   # Duplicate SarimaxModel entity (currently redundant)
├── core/
│   ├── config.py                   # Pipeline constants (horizon, seasonality, bounds)
│   └── exogenous.py                # PHHolidayEngine, TyphoonInjector, weekly_intensity
└── data/                           # Untracked folder for private CSV + SQLite DB
```

## Database Schema
**Database Details:** SQLite located at `data/xocompass_models.db` (hardcoded in repository).
**Table Name:** `sarimax_models`

**ORM Fields (repository & domain):**
* `id`: INTEGER (Primary Key)
* `model_name`: VARCHAR
* `model_binary`: BLOB (joblib blob)
* `pipeline_ver`: VARCHAR (default "v10.1")
* `train_end_date`: DATETIME
* `aic_score`: FLOAT
* `notes`: TEXT
* `created_at`: DATETIME (timezone-aware default)

**Dashboard Snapshot Fields (Nullable):**
* `total_records`: INTEGER
* `data_quality_pct`: FLOAT
* `revenue_total`: FLOAT
* `growth_rate_str`: VARCHAR
* `expected_bookings`: INTEGER
* `peak_travel_period`: VARCHAR

**Repository Functions (Read-Only):**
* `fetch_all_models_metadata()`
* `fetch_dashboard_stats(model_id)`
* `fetch_model_binary(model_id=None)` (deserializes joblib blob)

## Architecture Decisions
* **Weekly aggregation (daily→weekly)** → Reduces sparsity/zero inflation and stabilizes MSME booking counts.
* **SARIMAX over black-box models** → Provides "glass box" interpretability; coefficients can be discussed for natural-unit interpretation.
* **Remove log transform of target (log1p)** → Avoids Jensen bias and explosive asymmetric confidence intervals after inverse transform; model trains directly on raw weekly counts.
* **Typhoon “hangover” window = 2 weeks** → Treats typhoons as transient shocks; rolling max captures the storm week plus immediate recovery.
* **Seasonal period m=52** → Aligns with the annual weekly tourism cycle.
* **Order selection = ADF + optional auto_arima grid search** → Combines statistical diagnosis (Method A) with AIC stepwise seasonal search (Method B).
* **Layered backend separation** → Keeps heavy offline training (services/pipeline) strictly isolated from HTTP request handling (api).

## API Endpoints
* **GET /** → Redirect to Swagger UI (/docs) | status: done
* **GET /api/models** → List model registry metadata for dropdown | status: done
* **GET /api/dashboard-stats/{model_id}** → Return stored KPI snapshot for a model | status: done
* **POST /api/forecast** → Load model blob, run forecast, return array | status: done (exogenous and start date currently hardcoded)
* **POST /api/append_data** → Incremental update endpoint | status: planned

## ML Pipeline
**Entry Point:** `python -m services.pipeline.orchestrator`

**Pipeline Execution Steps:**
* **Step 1 (Ingestion):** Detects dates, aggregates to weekly (`W-MON`), builds target `y`, and generates exogenous features (holidays, typhoons, optimal lead search).
* **Step 3 (Stationarity):** Splits train/test (0.20 ratio), performs ADF iterative differencing, and runs optional seasonal grid search.
* **Step 5 (Training):** Fits `statsmodels` SARIMAX, runs residual diagnostics (Ljung-Box, Jarque-Bera), and interprets coefficients.
* **Step 6 (Evaluation):** Computes WMAPE/MAE/RMSE metrics and builds the future forecast JSON payload (16-week horizon).

**SARIMAX Specifics:**
* **Seasonal Period:** m=52
* **Train Window:** Determined dynamically by dataset range; chronological split.
* **Retraining Trigger:** Not currently implemented (no automated trigger in API).

**Exogenous Variables Mapped:**
* **Holidays (Pipeline):** `is_holiday`, `is_mega_holiday`, `is_long_weekend`, `holiday_intensity`, `holiday_lead_{OPTIMAL_LEAD}`.
* **Typhoons (Pipeline):** `typhoon_msw`, `typhoon_raw`.
* **Current Pipeline Training Set:** `ALL_EXOG = [lead_col, "is_long_weekend"]`
* **Current Inference Set:** Uses placeholders (`holiday_lead_1`, `is_long_weekend`) filled with zeros.

## Current Progress
### Done
* FastAPI app with CORS middleware and core endpoints.
* Repository read path for registry metadata, KPI snapshots, and joblib loading.
* 6-step offline pipeline runs end-to-end (produces fitted SARIMAX, metrics, JSON payload).
* Exogenous engines (holiday + typhoon) implemented.
* Pipeline configurations centralized.

### In Progress
* Aligning inference service with the trained model’s real exogenous schema and dates.
* Unifying DB schema/ORM redundancies between domain and repository folders.

### Blocked / Not Started
* Persisting training outputs into SQLite (pipeline currently does not write model blobs/metrics).
* Migration strategy for evolving SQLite schema safely.
* Production-ready deployment configuration (env-driven CORS, Render disk setup).
* Long-running job orchestration for UI-triggered training.

## Known Issues & Pending Decisions
* **Schema drift risk:** ORM may not match existing SQLite file; `create_all()` will not migrate existing tables.
* **Inference exog mismatch:** Service hardcodes `holiday_lead_1` and start date; trained models may expect different variables.
* **Hardcoded CORS:** Currently limited to localhost and one Vercel URL; needs to be env-driven.
* **Data Privacy:** Must ensure `data/` folder remains untracked and raw CSV rows are never logged.

## Coding Conventions
* **Layering:** `api/` orchestrates HTTP only. `services/` handles business logic. `repository/` handles persistence. `services/pipeline/` handles offline training.
* **Typing:** Use Python 3.10+ typing syntax (e.g., `int | None`) consistently.
* **Data Privacy:** Never hardcode or commit real client data. Avoid printing raw rows; print aggregate counts/metrics only.