from __future__ import annotations

# ─────────────────────────────────────────────────────────────────────────────
# CRITICAL: Set Matplotlib backend BEFORE any pipeline import.
# Prevents plt.show() from blocking in headless/server environments.
# ─────────────────────────────────────────────────────────────────────────────
import matplotlib
matplotlib.use("Agg")


import os
import sys
import json
import joblib
import numpy as np
import pandas as pd
import math
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

# ── Pipeline steps (treat as black boxes — do NOT modify these files) ──
from services.pipeline.step1_ingestion import run_step1_data_ingestion
from services.pipeline.step2_correlations import run_step2_correlations
from services.pipeline.step3_stationarity import run_step3_stationarity
from services.pipeline.step4_decomposition import run_step4_decomposition
from services.pipeline.step5_training import run_step5_training
from services.pipeline.step6_evaluation import run_step6_evaluation

# ── Database layer ──
from repository.model_repository import SessionLocal
from domain.models import (
    SarimaxModel,
    ModelDiagnostic,
    ForecastCache,
    TrainingDataLog,
)

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

ph_tz      = ZoneInfo("Asia/Manila")
MODEL_DIR  = Path("data/models")
EXPORT_CSV = Path("data/training_export.csv")


# ─────────────────────────────────────────────────────────────────────────────
# PRE-STEP: DB → CSV
# Queries training_data_log and reconstructs a flat transaction-level CSV
# so that run_step1_data_ingestion() can consume it unchanged.
# Each weekly row with booking_value=N is expanded into N individual rows,
# matching the raw transaction format the pipeline expects.
# ─────────────────────────────────────────────────────────────────────────────

def export_db_to_csv(csv_path: Path = EXPORT_CSV) -> str:
    print("\n📤 PRE-STEP: Exporting training_data_log → temporary CSV...")

    with SessionLocal() as db:
        records = (
            db.query(TrainingDataLog)
            .order_by(TrainingDataLog.record_date)
            .all()
        )

    if len(records) < 20:
        raise ValueError(
            f"Insufficient data: only {len(records)} rows in training_data_log. "
            "Upload the KJS CSV via POST /api/upload before running the pipeline."
        )

    # Expand weekly counts → individual transaction rows for step1's groupby logic.
    # Also carry the weekly_revenue forward so step1 can sum it correctly.
    rows = []
    for r in records:
        weekly_rev = None
        if r.additional_exog_json:
            weekly_rev = r.additional_exog_json.get("weekly_revenue")

        count = max(1, int(r.booking_value))
        # Distribute revenue evenly across expanded rows so sum is preserved
        per_row_rev = (weekly_rev / count) if weekly_rev else None

        for _ in range(count):
            row = {"Generation Date": r.record_date.strftime("%Y-%m-%d")}
            if per_row_rev is not None:
                row["Net Amount"] = round(per_row_rev, 4)
            rows.append(row)

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(csv_path, index=False)

    has_revenue = any("Net Amount" in r for r in rows)
    print(f"   ✅ Exported {len(rows):,} transaction rows "
          f"({len(records)} weekly buckets) → {csv_path}")
    print(f"   💰 Net Amount column included: {has_revenue}")
    latest_batch_id = None
    if records:
        latest_batch_id = records[-1].ingestion_batch_id
    print(f"   📦 Latest ingestion_batch_id: {latest_batch_id}")
    return str(csv_path), latest_batch_id

# ─────────────────────────────────────────────────────────────────────────────
# POST-STEP 5: Model file persistence (File-First pattern)
# ─────────────────────────────────────────────────────────────────────────────

def save_model_file(fitted_model) -> Path:
    """
    Serializes the fitted SARIMAX result object to disk with joblib.
    Must be called BEFORE any database write (File-First, DB-Second pattern).
    If the subsequent DB write fails, the caller is responsible for unlinking
    the orphaned file.

    Returns the Path of the saved .joblib file.
    """
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    timestamp  = datetime.now(ph_tz).strftime("%Y%m%d_%H%M%S")
    model_path = MODEL_DIR / f"sarimax_{timestamp}.joblib"

    print(f"\n💾 Serializing model → {model_path}")
    joblib.dump(fitted_model, model_path)
    print(f"   ✅ Model saved ({model_path.stat().st_size / 1024:.1f} KB)")
    return model_path


# ─────────────────────────────────────────────────────────────────────────────
# HELPER: Residual formatting
# Converts statsmodels fittedvalues + resid into the [{fitted, residual}]
# shape required by ModelDiagnostic.residuals_json and the Pydantic schema.
# ─────────────────────────────────────────────────────────────────────────────

def format_residuals(step5: dict) -> list[dict]:
    """
    Returns a list of {"fitted": float, "residual": float} dicts,
    excluding any NaN pairs that appear at the start of differenced series.
    """
    fitted_vals = step5["fitted"].fittedvalues.clip(lower=0)
    residuals   = step5["fitted"].resid
    return [
        {"fitted": round(float(f), 4), "residual": round(float(r), 4)}
        for f, r in zip(fitted_vals, residuals)
        if not (np.isnan(f) or np.isnan(r))
    ]


# ─────────────────────────────────────────────────────────────────────────────
# HELPER: ACF / PACF formatting
# Computes lag-correlation arrays on the model residuals and returns the
# [{lag, value}] shape required by ModelDiagnostic and the Pydantic schema.
# ─────────────────────────────────────────────────────────────────────────────

def format_acf_pacf(
    step5: dict,
    nlags: int = 20,
) -> tuple[list[dict], list[dict]]:
    """
    Returns (acf_out, pacf_out) where each is a list of
    {"lag": int, "value": float} dicts, capped at nlags or len(resid)//2-1.
    """
    from statsmodels.tsa.stattools import acf, pacf

    res = step5["fitted"].resid.dropna()
    nl  = min(nlags, len(res) // 2 - 1)

    acf_vals  = acf(res, nlags=nl, fft=True)
    pacf_vals = pacf(res, nlags=nl, method="ywm")

    acf_out  = [{"lag": i, "value": round(float(v), 4)} for i, v in enumerate(acf_vals)]
    pacf_out = [{"lag": i, "value": round(float(v), 4)} for i, v in enumerate(pacf_vals)]
    return acf_out, pacf_out


# ─────────────────────────────────────────────────────────────────────────────
# HELPER: ADF on residuals
# Re-runs the ADF test on model residuals (not the raw series) to confirm
# that the fitted model has successfully removed the unit root.
# ─────────────────────────────────────────────────────────────────────────────

def compute_adf_on_residuals(step5: dict) -> tuple[float, float, str]:
    """
    Returns (adf_stat, adf_pvalue, adf_conclusion) for residual stationarity.
    """
    from statsmodels.tsa.stattools import adfuller

    res        = step5["fitted"].resid.dropna()
    stat, pval = adfuller(res, autolag="AIC")[:2]
    conclusion = (
        "Stationary (residuals are white noise)"
        if pval <= 0.05
        else "Non-stationary residuals — consider higher differencing order"
    )
    return float(stat), float(pval), conclusion


# ─────────────────────────────────────────────────────────────────────────────
# HELPER: Ljung-Box and Jarque-Bera extraction
# Step 5 already prints these but does not return them in its dict.
# We recompute here from the fitted object so the harness is self-contained.
# ─────────────────────────────────────────────────────────────────────────────

def compute_lb_jb(step5: dict) -> tuple[float, float, str, float, float, str]:
    """
    Returns:
        lb_stat, lb_pvalue, lb_conclusion,
        jb_stat, jb_pvalue, jb_conclusion
    """
    from statsmodels.stats.diagnostic import acorr_ljungbox
    from statsmodels.stats.stattools import jarque_bera as _jb

    res = step5["fitted"].resid.dropna()

    # Ljung-Box: use lags=[5, 10], take the last (most conservative) p-value
    lbs    = [lag for lag in [5, 10] if lag < len(res) // 2]
    lb_df  = acorr_ljungbox(res, lags=lbs, return_df=True)
    lb_stat = float(lb_df["lb_stat"].iloc[-1])
    lb_p    = float(lb_df["lb_pvalue"].iloc[-1])
    lb_conc = (
        "White noise — no significant autocorrelation in residuals"
        if lb_p > 0.05
        else "Autocorrelation detected — residuals are not white noise"
    )

    # Jarque-Bera
    jb_stat, jb_p = _jb(res)[:2]
    jb_conc = (
        "Residuals approximately normal"
        if jb_p > 0.05
        else "Non-normal residuals — skewness or excess kurtosis present"
    )

    return lb_stat, lb_p, lb_conc, float(jb_stat), float(jb_p), jb_conc


# ─────────────────────────────────────────────────────────────────────────────
# HELPER: KPI snapshot computation
# Derives the Page 1 dashboard card values from pipeline outputs.
# Revenue is a proxy: mean weekly bookings × PHP 4,500 avg ticket × 52 weeks.
# ─────────────────────────────────────────────────────────────────────────────

def compute_snapshot_kpis(
    step1: dict,
    step6: dict,
    model_id: int,
) -> dict:
    """
    Returns a dict of KPI values ready for ForecastSnapshot insertion.
    All values are Python-native types (not NumPy scalars) for SQLAlchemy safety.
    """
    w           = step1["weekly_df"]
    df_raw      = step1["df_raw"]
    forecast_df = step6["forecast_df"]

    total_records  = int(len(df_raw))

    nonzero_weeks  = int((w["bookings_weekly"] > 0).sum())
    data_quality   = round(float(nonzero_weeks / total_records * 100), 1)

    avg_weekly     = float(w["bookings_weekly"].mean())

    # REVENUE
    if step1.get("revenue_total") is not None:
        revenue_total = step1["revenue_total"]
    else:
        avg_weekly    = float(w["bookings_weekly"].mean())
        revenue_total = round(avg_weekly * 4_500 * 52, 2)
        print(f"   ⚠️  Revenue proxy used: ₱{revenue_total:,.2f}")


    # yearly growth rate
    if len(w) >= 104: 
        recent_yr = float(w["bookings_weekly"].iloc[-52:].sum())
        prior_yr  = float(w["bookings_weekly"].iloc[-104:-52].sum())
        growth  = round((recent_yr - prior_yr) / prior_yr * 100, 1) if prior_yr > 0 else 0.0
    else:
        # Fallback if the dataset is smaller than 2 years: Half-Year vs Prior Half-Year
        recent_half = float(w["bookings_weekly"].iloc[-26:].sum())
        prior_half  = float(w["bookings_weekly"].iloc[-52:-26].sum())
        growth  = round((recent_half - prior_half) / prior_half * 100, 1) if prior_half > 0 else 0.0

    # Exact Yearly Bookings from Raw Data
    # Group by the exact year of the raw Booking_Date
    yearly_counts = df_raw.groupby(df_raw["Booking_Date"].dt.year).size()
    
    # Format it exactly how the frontend expects it
    yearly_bookings_list = [
        {"year": str(int(year)), "bookings": int(count)}
        for year, count in yearly_counts.items()
    ]

    expected       = int(forecast_df["rounded"].sum())
    peak_idx       = forecast_df["forecast"].idxmax()
    peak_period    = peak_idx.strftime("%B %Y")

    return {
        "model_id":           model_id,
        "total_records":      total_records,
        "data_quality_pct":   data_quality,
        "revenue_total":      revenue_total,
        "growth_rate":        growth,
        "expected_bookings":  expected,
        "peak_travel_period": peak_period,
        "yearly_bookings":      yearly_bookings_list, 
    }


# ─────────────────────────────────────────────────────────────────────────────
# ATOMIC DB WRITE
# All five tables are written inside a single SQLAlchemy transaction.
# If any insert fails, the transaction rolls back automatically (context manager)
# and the orphaned .joblib file is deleted to restore a clean state.
# ─────────────────────────────────────────────────────────────────────────────

def persist_to_neon(
    model_path: Path,
    step1: dict,
    step2: dict,
    step3: dict,
    step5: dict,
    step6: dict,
) -> int:
    """
    Writes the full ML output set to Neon in one atomic transaction:
        sarimax_models      ← model metadata + metrics
        model_diagnostics   ← residuals, ACF/PACF, statistical test results
        forecast_cache      ← 16-week forward predictions + CI bounds
        forecast_snapshots  ← Page 1 KPI card values

    Returns the newly created model_id on success.
    Raises and cleans up the orphaned .joblib file on any failure.
    """
    print("\n🗃️  Persisting pipeline outputs to Neon (atomic transaction)...")

    # ── Unpack orders ────────────────────────────────────────────────────────
    order      = step5["best_order"]
    seasonal   = step5["best_seas"]
    p, d, q    = order
    sp, sd, sq, _ = seasonal

    # ── Compute all diagnostic values before opening the session ─────────────
    metrics              = step6["test_metrics"]["weekly"]
    residuals            = format_residuals(step5)
    acf_out, pacf_out    = format_acf_pacf(step5)
    adf_stat, adf_p, adf_conc = compute_adf_on_residuals(step5)
    lb_stat, lb_p, lb_conc, jb_stat, jb_p, jb_conc = compute_lb_jb(step5)
    forecast_df          = step6["forecast_df"]

    try:
        with SessionLocal() as db:

            # 1. Deactivate every previously active model
            db.query(SarimaxModel).filter(
                SarimaxModel.is_active == True
            ).update({"is_active": False})

            # 2. Insert new model metadata row
            new_model = SarimaxModel(
                model_name=f"SARIMAX_{datetime.now(ph_tz).strftime('%Y%m%d_%H%M%S')}",
                model_path=str(model_path),
                is_active=True,
                pipeline_ver="v10.1",
                p=int(p), d=int(d), q=int(q),
                seasonal_p=int(sp), seasonal_d=int(sd), seasonal_q=int(sq),
                exog_features_json=step1["ALL_EXOG"],
                aic_score=float(step5["fitted"].aic),
                bic_score=float(step5["fitted"].bic),
                mae=float(metrics["mae"]),
                rmse=float(metrics["rmse"]),
                mape=0.0,   # undefined when actuals can be 0; WMAPE is the primary metric
                wmape=float(metrics["wmape"]),
                train_start_date=step3["train_wk"].index.min().replace(tzinfo=ph_tz),
                train_end_date=step3["train_wk"].index.max().replace(tzinfo=ph_tz),
                created_at=datetime.now(ph_tz),
                ingestion_batch_id=step1.get("ingestion_batch_id"),

            )
            db.add(new_model)
            db.flush()  # assigns new_model.id without committing the transaction
            model_id = new_model.id

            # 3. Insert diagnostics
            db.add(ModelDiagnostic(
                model_id=model_id,
                residuals_json=residuals,
                acf_values_json=acf_out,
                pacf_values_json=pacf_out,
                correlation_json=step2["correlation_heatmap"],
                adf_stat=adf_stat,
                adf_pvalue=adf_p,
                adf_conclusion=adf_conc,
                ljungbox_stat=lb_stat,
                ljungbox_pvalue=lb_p,
                ljungbox_conclusion=lb_conc,
                jarquebera_stat=jb_stat,
                jarquebera_pvalue=jb_p,
                jarquebera_conclusion=jb_conc,
                validation_graph_json=step6["validation_graph"],    # ── NEW
            ))

            # 4. Clear any stale forecast rows for this model, then insert fresh ones
            db.query(ForecastCache).filter(
                ForecastCache.model_id == model_id
            ).delete()

            # ── Existing: persist forward forecast rows ───────────────────
            forecast_data = json.loads(step6["forecast_json"])
            for i, item in enumerate(forecast_data):
                db.add(ForecastCache(
                    model_id=model_id,
                    forecast_date=pd.Timestamp(item["week_start"]).to_pydatetime(),
                    predicted=item["forecast_bookings"],
                    lower_bound=item["confidence_lower_95"],
                    upper_bound=item["confidence_upper_95"],
                    periods_ahead=i + 1,
                    risk_flag="MEDIUM",                      # Q4: placeholder
                    confidence_tier=item["confidence_tier"], # ── NEW
                    generated_at=datetime.now(ph_tz),
                ))

            # ── NEW: persist trailing 4 backtest rows ─────────────────────
            # periods_ahead is negative to distinguish from forward rows.
            # -4 = 4 weeks before forecast start, -1 = 1 week before.
            backtest_data = json.loads(step6["backtest_json"])
            for i, item in enumerate(backtest_data):
                db.add(ForecastCache(
                    model_id=model_id,
                    forecast_date=pd.Timestamp(item["week_start"]).to_pydatetime(),
                    predicted=item["forecast_bookings"],
                    lower_bound=item["confidence_lower_95"],
                    upper_bound=item["confidence_upper_95"],
                    periods_ahead=-(4 - i),
                    risk_flag=None,
                    confidence_tier="BACKTEST",
                    actual_bookings=item.get("actual_bookings"),  # ── NEW
                    generated_at=datetime.now(ph_tz),
                ))
            # 5. Insert KPI snapshot for Page 1 dashboard cards
            kpis = compute_snapshot_kpis(step1, step6, model_id)
            new_model.total_records = kpis["total_records"]
            new_model.data_quality_pct = kpis["data_quality_pct"]
            new_model.revenue_total = kpis["revenue_total"]
            new_model.growth_rate = kpis["growth_rate"]
            new_model.yearly_bookings_json = kpis["yearly_bookings"]
            new_model.expected_bookings = kpis["expected_bookings"]
            new_model.peak_travel_period = kpis["peak_travel_period"]

            db.commit()  # ← single commit: all tables succeed or none do
            print(f"   ✅ All tables committed. model_id={model_id}")
            return model_id

    except Exception as exc:
        # DB write failed → remove the orphaned .joblib to keep disk + DB in sync
        print(f"\n❌ DB write failed: {exc}")
        print(f"   Removing orphaned model file: {model_path}")
        model_path.unlink(missing_ok=True)
        raise

def fetch_revenue_from_db() -> float | None:
    with SessionLocal() as db:
        records = db.query(TrainingDataLog).all()

    revenues = [r.weekly_revenue for r in records if r.weekly_revenue is not None]

    if not revenues:
        print("   ⚠️  No weekly_revenue found in DB. Will use booking-proxy fallback.")
        return None

    total = sum(revenues)
    print(f"   💰 Revenue from DB: ₱{total:,.2f}")
    return total

# ─────────────────────────────────────────────────────────────────────────────
# NEW: UI CONFIG BUILDER
# ─────────────────────────────────────────────────────────────────────────────
def build_pipeline_config(config: dict) -> dict:
    """Translates UI JSON into pipeline logic flags."""
    model = config.get("model_selection", "SARIMAX")
    factors = set(config.get("external_factors", []))
    raw_period = config.get("time_period", "Whole Data Set (2013-Present)")

    if raw_period == "7 Days": days = 7
    elif raw_period == "14 Days": days = 14
    elif raw_period == "21 Days": days = 21
    elif raw_period == "30 Days": days = 30
    elif raw_period == "90 Days": days = 90
    else: days = 112 # Default 16 weeks

    return {
        "use_exog": model == "SARIMAX",
        "use_holiday": "Holiday" in factors and model == "SARIMAX",
        "use_seasonality": model in ("SARIMA", "SARIMAX"),
        "forced_seasonal_order": (0, 0, 0, 0) if model == "ARIMA" else None,
        "forecast_steps": math.ceil(days / 7)
    }

# ─────────────────────────────────────────────────────────────────────────────
# MAIN EXECUTION BLOCK (UPDATED WITH UI INTERCEPTS)
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("█" * 75)
    print("   XoCompass v10.1 — Production Orchestrator")
    print("█" * 75)

    # 1. Read UI Payload
    raw_config = json.loads(sys.stdin.read()) if not sys.stdin.isatty() else {}
    pipeline_cfg = build_pipeline_config(raw_config)

    # 2. Extract Data
    csv_path, latest_batch_id = export_db_to_csv(EXPORT_CSV)
    step1 = run_step1_data_ingestion(csv_path)
    step1["revenue_total"] = fetch_revenue_from_db()
    step1["ingestion_batch_id"] = latest_batch_id

    # 3. INTERCEPT 1: Exogenous Filtering
    if not pipeline_cfg["use_exog"]:
        step1["ALL_EXOG"] = []          
    elif not pipeline_cfg["use_holiday"]:
        step1["ALL_EXOG"] = [c for c in step1["ALL_EXOG"] if "is_long_weekend" not in c.lower() and "holiday" not in c.lower()]

    # 4. Math Steps
    step2 = run_step2_correlations(step1)
    step3 = run_step3_stationarity(step1)

    # 5. INTERCEPT 2: Seasonality
    if pipeline_cfg["forced_seasonal_order"] is not None:
        step3["final_seasonal"] = pipeline_cfg["forced_seasonal_order"]
    elif not pipeline_cfg["use_seasonality"]:
        step3["final_seasonal"] = (0, 0, 0, 0)

    # 6. Final Steps & Save
    step4 = run_step4_decomposition(step1)
    step5 = run_step5_training(step1, step3)
    
    model_path = save_model_file(step5["fitted"])
    
    # 7. Pass dynamic steps to Eval
    step6 = run_step6_evaluation(step1, step3, step5, forecast_steps=pipeline_cfg["forecast_steps"])
    
    model_id = persist_to_neon(model_path, step1, step2, step3, step5, step6)
    print(f"\n   Pipeline complete. model_id = {model_id}")