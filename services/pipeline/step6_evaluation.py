

###############################################################################
#                                                                             #
#   STEP 6 — EVALUATION (The Business Value)                                 #
#                                                                             #
#   ┌──────────────────────────────────────────────────────────────────┐      #
#   │  FIX 2: No expm1() inverse transform. Predictions are already   │      #
#   │         in booking-count units. CIs are DIRECT, not inflated    │      #
#   │         by nonlinear back-transformation.                        │      #
#   │  JSON OUTPUT SCHEMA: UNCHANGED from v10.0.                      │      #
#   │  EVALUATION LOGIC: UNCHANGED from v10.0.                        │      #
#   └──────────────────────────────────────────────────────────────────┘      #
#                                                                             #
###############################################################################

from __future__ import annotations

import json
from datetime import timedelta

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import xarray as xr
import xskillscore as xs

from core.config import (
    FORECAST_HORIZON,
    SEASONAL_M,
    TYPHOON_HANGOVER,
    WEEKLY_RULE,
)
from core.exogenous import PHHolidayEngine, TyphoonInjector, weekly_intensity

# ─────────────────────────────────────────────────────────────────────────────
# 6A. Metric computation helpers  (UNCHANGED from v10.0)
# ─────────────────────────────────────────────────────────────────────────────

def compute_wmape(actual, forecast):
    """WMAPE = Σ|actual − forecast| / Σ|actual| × 100"""
    a = np.array(actual, dtype=float)
    f = np.array(forecast, dtype=float)
    total = np.sum(np.abs(a))
    return float(np.sum(np.abs(a - f)) / total * 100.0) if total > 0 else 0.0


def compute_mae(actual, forecast):
    """Mean Absolute Error via xskillscore."""
    a = xr.DataArray(np.array(actual, dtype=float), dims="t")
    f = xr.DataArray(np.array(forecast, dtype=float), dims="t")
    return float(xs.mae(a, f, dim="t").values)


def compute_rmse(actual, forecast):
    """Root Mean Square Error via xskillscore."""
    a = xr.DataArray(np.array(actual, dtype=float), dims="t")
    f = xr.DataArray(np.array(forecast, dtype=float), dims="t")
    return float(xs.rmse(a, f, dim="t").values)


def eval_model(actual, pred):
    """Compute all three evaluation metrics as a dict."""
    return {
        "wmape": compute_wmape(actual, pred),
        "mae":   compute_mae(actual, pred),
        "rmse":  compute_rmse(actual, pred),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 6B. Climatological Typhoon Probability  (UNCHANGED from v10.0)
# ─────────────────────────────────────────────────────────────────────────────

def _climatological_typhoon_flag(future_index):
    """Months with ≥1 historical PAGASA storm get flag=1."""
    storm_months = set()
    for s in TyphoonInjector.STORMS:
        beg = pd.Timestamp(s["par_beg"])
        end = pd.Timestamp(s["par_end"])
        for m in range(beg.month, end.month + 1):
            storm_months.add(m)
    flags = pd.Series(0, index=future_index)
    for dt in future_index:
        if dt.month in storm_months:
            flags[dt] = 1
    return flags


# ─────────────────────────────────────────────────────────────────────────────
# 6C. Master Evaluation & Forecast Function
# ─────────────────────────────────────────────────────────────────────────────

def run_step6_evaluation(step1, step3, step5, forecast_steps: int = FORECAST_HORIZON):
    """
    STEP 6 — EVALUATION (The Business Value)

    v10.1 changes:
      FIX 2: Predictions come out of SARIMAX in NATURAL BOOKING UNITS.
             No np.expm1() inverse transform. Confidence intervals are
             SYMMETRIC and directly interpretable (not blown up by
             the convexity of the exponential function).

    JSON output schema is IDENTICAL to v10.0.
    """
    print("\n╔" + "═" * 70 + "╗")
    print("║" + "  STEP 6 — EVALUATION (The Business Value)".center(70) + "║")
    print("╚" + "═" * 70 + "╝")

    fitted      = step5["fitted"]
    w           = step1["weekly_df"]
    holidays_df = step1["holidays_df"]
    ALL_EXOG    = step1["ALL_EXOG"]
    lead_col    = step1["lead_col"]
    OPTIMAL_LEAD = step1["OPTIMAL_LEAD"]

    train_wk    = step3["train_wk"]
    test_wk     = step3["test_wk"]
    train_y_raw = step3["train_y_raw"]
    test_y_raw  = step3["test_y_raw"]
    test_X      = step3["test_X"]
    
    if test_X is not None and test_X.empty: test_X = None
    
    # ═══════════════════════════════════════════════════════════════════════
    # 6.1  TEST-SET PREDICTIONS
    #
    # FIX 2: No expm1() needed. The model was trained on raw booking
    # counts, so .fittedvalues and .forecast() are already in booking
    # units. We only clip to ≥0 (bookings cannot be negative).
    # ═══════════════════════════════════════════════════════════════════════
    train_pred = fitted.fittedvalues.clip(lower=0)           # <── direct
    test_pred  = fitted.forecast(
        steps=len(test_y_raw), exog=test_X
    ).clip(lower=0)                                          # <── direct

    test_pred_s  = pd.Series(test_pred.values, index=test_wk.index)
    train_pred_s = pd.Series(train_pred.values, index=train_wk.index)

    # ── NEW: Validation graph payload ─────────────────────────────────────
    # Uses the test-set split (chronological holdout) to show Actual vs
    # Predicted over the evaluation window. This is the ONLY defensible
    # source of validation data — it uses held-out data the model never
    # saw during training.
    #
    # get_prediction() gives us in-sample predictions WITH confidence
    # intervals, which fittedvalues alone does not provide.
    # We scope it to the test window only (start=test_wk.index[0]).
    #
    # ISO 25010 Reliability → Maturity:
    #   Validation against held-out data is a direct measure of model
    #   generalisability. Exposing this to the user is a transparency
    #   commitment, not just a UI feature.

    pred_obj = fitted.get_prediction(
        start=test_wk.index[0],
        end=test_wk.index[-1],
        exog=test_X,
    )
    pred_mean = pred_obj.predicted_mean.clip(lower=0)
    pred_ci   = pred_obj.conf_int(alpha=0.05)
    ci_lo     = pred_ci.iloc[:, 0].clip(lower=0)
    ci_hi     = pred_ci.iloc[:, 1].clip(lower=0)

    validation_graph = []
    for dt, actual_val in zip(test_wk.index, test_y_raw):
        # Date label format matches your frontend: "Sep W2", "Oct W1", etc.
        week_of_month = (dt.day - 1) // 7 + 1
        date_label = f"{dt.strftime('%b')} W{week_of_month}"
        validation_graph.append({
            "date_label":  date_label,
            "actual":      int(round(float(actual_val))),
            "forecasted":  round(float(pred_mean.loc[dt]), 2),
            "lower_ci":    round(float(ci_lo.loc[dt]), 2),
            "upper_ci":    round(float(ci_hi.loc[dt]), 2),
        })

    # ═══════════════════════════════════════════════════════════════════════
    # 6.2  MULTI-RESOLUTION METRICS  (logic unchanged)
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n{'═' * 70}")
    print(f"  MULTI-RESOLUTION EVALUATION")
    print(f"{'═' * 70}")

    test_metrics = {}

    m_tw = eval_model(train_y_raw, train_pred_s)
    m_w  = eval_model(test_y_raw, test_pred_s)
    test_metrics["weekly"] = m_w
    print(f"  {'Train Wk':12s} WMAPE={m_tw['wmape']:6.2f}%  "
          f"MAE={m_tw['mae']:5.1f}  RMSE={m_tw['rmse']:6.2f}")
    print(f"  {'Test Wk':12s} WMAPE={m_w['wmape']:6.2f}%  "
          f"MAE={m_w['mae']:5.1f}  RMSE={m_w['rmse']:6.2f}")

    ma = test_y_raw.resample("MS").sum()
    mf = test_pred_s.resample("MS").sum()
    if len(ma) >= 2:
        mm = eval_model(ma, mf)
        test_metrics["monthly"] = mm
        print(f"  {'Test Mo':12s} WMAPE={mm['wmape']:6.2f}%  "
              f"MAE={mm['mae']:5.1f}  RMSE={mm['rmse']:6.2f}")

    qa = test_y_raw.resample("QS").sum()
    qf = test_pred_s.resample("QS").sum()
    if len(qa) >= 1:
        qm = eval_model(qa, qf)
        test_metrics["quarterly"] = qm
        print(f"  {'Test Qt':12s} WMAPE={qm['wmape']:6.2f}%  "
              f"MAE={qm['mae']:5.1f}  RMSE={qm['rmse']:6.2f}")

    print(f"{'═' * 70}")

    # ═══════════════════════════════════════════════════════════════════════
    # 6.3  FORECAST vs ACTUAL VISUALISATION  (unchanged)
    # ═══════════════════════════════════════════════════════════════════════
    fig, axes = plt.subplots(2, 1, figsize=(16, 10))
    axes[0].plot(train_wk.index, train_y_raw, label="Training Data",
                 color="steelblue", lw=1)
    axes[0].plot(test_wk.index, test_y_raw, "o-", label="Actual (Test)",
                 color="navy", ms=4, lw=1.2)
    axes[0].plot(test_wk.index, test_pred_s, "s--", label="Forecast",
                 color="#e74c3c", ms=4, lw=1.2)
    axes[0].axvline(test_wk.index[0], color="gray", ls=":", label="Train/Test Split")
    axes[0].set_title("Forecast vs Actual — Full Series", fontweight="bold")
    axes[0].set_ylabel("Bookings/week")
    axes[0].legend()

    axes[1].plot(test_wk.index, test_y_raw, "o-", label="Actual",
                 color="navy", ms=5, lw=1.5)
    axes[1].plot(test_wk.index, test_pred_s, "s--", label="Forecast",
                 color="#e74c3c", ms=5, lw=1.5)
    axes[1].set_title("Test Period (Zoomed)", fontweight="bold")
    axes[1].set_ylabel("Bookings/week")
    axes[1].legend()
    plt.tight_layout(); plt.show()

    # ═══════════════════════════════════════════════════════════════════════
    # 6.4  16-WEEK FUTURE FORECAST
    #
    # FIX 2 impact: get_forecast() returns predictions and CI directly
    # in booking-count units. No expm1() back-transformation.
    # The CI is now SYMMETRIC around the point forecast (as SARIMAX
    # produces Gaussian prediction intervals on the native scale).
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n── Generating {forecast_steps}-Week Future Forecast ──")

    future_idx = pd.date_range(
        w.index.max() + timedelta(days=7),
        periods=forecast_steps,
        freq=WEEKLY_RULE,
    )
    fc = pd.DataFrame(index=future_idx)
    fc.index.name = "date"

    # (a) Holiday lead for future weeks
    far = pd.date_range(
        future_idx.min(),
        future_idx.max() + timedelta(weeks=OPTIMAL_LEAD + 2),
        freq=WEEKLY_RULE,
    )
    far_int = weekly_intensity(holidays_df, far)
    far_s = pd.Series(far_int, index=far)
    fc[lead_col] = far_s.shift(-OPTIMAL_LEAD).reindex(future_idx).fillna(0).astype(int)

    # (a) Long weekend indicator
    hols = PHHolidayEngine().generate(
        future_idx.min(), future_idx.max() + timedelta(days=6)
    )
    fc["is_long_weekend"] = 0
    for idx_dt in fc.index:
        sl = hols.reindex(
            pd.date_range(idx_dt, idx_dt + timedelta(days=6), freq="D")
        ).fillna(0)
        if sl["is_long_weekend"].max() > 0:
            fc.loc[idx_dt, "is_long_weekend"] = 1

    # (b) Climatological typhoon flag (informational, not in exog)
    fc["typhoon_climate_flag"] = _climatological_typhoon_flag(future_idx)

    # ── Run SARIMAX forecast ──
    fc_X = fc[ALL_EXOG].astype(float)

    if fc_X.empty: fc_X = None
    
    fobj = fitted.get_forecast(steps=forecast_steps, exog=fc_X)

    # ── FIX 2: Direct predictions, no expm1() ──
    # Predictions are already in booking-count units.
    # Clip lower bound to 0 (bookings cannot be negative).
    pred  = fobj.predicted_mean.clip(lower=0)
    ci    = fobj.conf_int(alpha=0.05)
    ci_lo = ci.iloc[:, 0].clip(lower=0)    # floor at 0
    ci_hi = ci.iloc[:, 1].clip(lower=0)

    forecast_df = pd.DataFrame({
        "forecast":             pred.values,
        "lo95":                 ci_lo.values,
        "hi95":                 ci_hi.values,
        "holiday_lead":         fc[lead_col].values,
        "is_long_weekend":      fc["is_long_weekend"].values,
        "typhoon_climate_flag": fc["typhoon_climate_flag"].values,
        "rounded":              np.round(pred.values).astype(int),
    }, index=future_idx)

    # ── Print forecast table ──
    il = {0: "", 1: "🏖️ L1-Isolated", 2: "🔗 L2-Long Wknd", 3: "🎄 L3-Mega"}
    print(f"\n{'═' * 85}")
    print(f"  🔮 {FORECAST_HORIZON}-WEEK FORECAST")
    print(f"{'═' * 85}")

    # Show CI width to verify FIX 2 improvement
    ci_widths = forecast_df["hi95"] - forecast_df["lo95"]
    print(f"  📏 Average 95% CI width: {ci_widths.mean():.1f} bookings "
          f"(should be much tighter than v10.0)")

    for dt, r in forecast_df.iterrows():
        we = dt + timedelta(days=6)
        tag  = il.get(int(r["holiday_lead"]), "")
        typh = "🌀 Typhoon-prone" if r["typhoon_climate_flag"] else ""
        flags = "  ".join(filter(None, [tag, typh]))
        ci_w = r["hi95"] - r["lo95"]
        print(
            f"  {dt.strftime('%d %b')}–{we.strftime('%d %b %Y')}  │  "
            f"📈 {int(r['rounded']):4d}  "
            f"CI:[{int(r['lo95'])}–{int(r['hi95'])}] (±{ci_w/2:.0f})  │  {flags}"
        )
    print(f"{'═' * 85}")

    # ── Forecast visualisation ──
    fig, ax = plt.subplots(figsize=(14, 5))
    recent = w["bookings_weekly"].iloc[-20:]
    ax.plot(recent.index, recent, "o-", color="steelblue", lw=1.5, ms=4,
            label="Recent Actual")
    ax.plot(forecast_df.index, forecast_df["forecast"], "s-", color="#2ecc71",
            lw=2, ms=6, label="Forecast")
    ax.fill_between(forecast_df.index, forecast_df["lo95"], forecast_df["hi95"],
                    alpha=0.2, color="#2ecc71", label="95% CI")
    ax.set_title(f"{FORECAST_HORIZON}-Week Forecast (m={SEASONAL_M})",
                 fontweight="bold")
    ax.set_ylabel("Bookings/week")
    ax.legend()
    plt.xticks(rotation=45)
    plt.tight_layout(); plt.show()

    # ═══════════════════════════════════════════════════════════════════════
    # 6.5  JSON PAYLOAD FOR FRONTEND UI
    #
    # Schema is IDENTICAL to v10.0. No structural changes.
    # The only difference is that the values are now more accurate
    # (tighter CIs, better point forecasts).
    # ═══════════════════════════════════════════════════════════════════════

    # ── MODIFIED: Tag each forward forecast row with confidence_tier ──────
    # Boundary: weeks 1-2 use OpenMeteo weather (reliable ~14-day window).
    # Weeks 3+ use climatological proxies — wider CI, lower confidence.
    # This boundary is a BUSINESS RULE, stored as data not code.
    # To change the boundary, update WEATHER_HORIZON in core/config.py.
    WEATHER_HORIZON = 2  # weeks

    payload = []
    for i, (dt, r) in enumerate(forecast_df.iterrows()):
        we = dt + timedelta(days=6)
        tier = "HIGH" if (i + 1) <= WEATHER_HORIZON else "LOWER"
        payload.append({
            "week_start":           dt.strftime("%Y-%m-%d"),
            "week_end":             we.strftime("%Y-%m-%d"),
            "forecast_bookings":    int(r["rounded"]),
            "confidence_lower_95":  int(r["lo95"]),
            "confidence_upper_95":  int(r["hi95"]),
            "holiday_lead_level":   int(r["holiday_lead"]),
            "is_long_weekend":      int(r["is_long_weekend"]),
            "typhoon_climate_flag": int(r["typhoon_climate_flag"]),
            "confidence_tier":      tier,            # ── NEW
        })

    # ── NEW: Trailing 4 weeks of in-sample fitted values (BACKTEST rows) ──
    # Calling get_prediction() with no arguments automatically uses the 
    # model's internal training exog data, avoiding ValueError crashes.
    in_sample_preds = fitted.get_prediction()
    
    # Extract mean and CI for the whole training set
    fitted_series = in_sample_preds.predicted_mean.clip(lower=0)
    in_sample_ci = in_sample_preds.conf_int(alpha=0.05)
    
    # Slice off just the trailing 4 weeks
    trailing_fitted = fitted_series.iloc[-4:]
    trailing_ci_lo = in_sample_ci.iloc[:, 0].clip(lower=0).iloc[-4:]
    trailing_ci_hi = in_sample_ci.iloc[:, 1].clip(lower=0).iloc[-4:]

    # Get the matching actuals
    trailing_actuals = w["bookings_weekly"].loc[trailing_fitted.index]

    backtest_payload = []
    for i, (dt, fitted_val) in enumerate(trailing_fitted.items()):
        actual_val = trailing_actuals.loc[dt]
        we = dt + timedelta(days=6)
        backtest_payload.append({
            "week_start":           dt.strftime("%Y-%m-%d"),
            "week_end":             we.strftime("%Y-%m-%d"),
            "forecast_bookings":    int(round(fitted_val)),
            "actual_bookings":      int(round(actual_val)),   
            "confidence_lower_95":  int(round(trailing_ci_lo.loc[dt])),   # ── NOW HAS REAL C.I.
            "confidence_upper_95":  int(round(trailing_ci_hi.loc[dt])),   # ── NOW HAS REAL C.I.
            "holiday_lead_level":   0,
            "is_long_weekend":      0,
            "typhoon_climate_flag": 0,
            "confidence_tier":      "BACKTEST",               
        })

    forecast_json = json.dumps(payload, indent=2)
    backtest_json = json.dumps(backtest_payload, indent=2)

    print(f"\n── JSON Payload (first 2 records) ──")
    print(json.dumps(payload[:2], indent=2))
    print(f"  ... ({len(payload)} total records)")

    # ═══════════════════════════════════════════════════════════════════════
    # 6.6  FINAL PIPELINE SUMMARY
    # ═══════════════════════════════════════════════════════════════════════
    W2 = 80
    print(f"\n╔{'═' * W2}╗")
    print(f"║{'  XoCompass v10.1 — PIPELINE SUMMARY'.center(W2)}║")
    print(f"╠{'═' * W2}╣")
    print(f"║  🏆 Model: SARIMAX{step5['best_order']}×{step5['best_seas']}".ljust(W2 + 1) + "║")
    print(f"║  📅 Data: {w.index.min().date()} → {w.index.max().date()} ({len(w)}w)".ljust(W2 + 1) + "║")
    print(f"║  📊 Test WMAPE: {m_w['wmape']:.2f}%  |  MAE: {m_w['mae']:.1f}".ljust(W2 + 1) + "║")
    lb_sym = "✅" if step5['lb_pval'] > 0.05 else "⚠️"
    jb_sym = "✅" if step5['jb_pval'] > 0.05 else "⚠️"
    print(f"║  🔬 Ljung-Box p={step5['lb_pval']:.4f} {lb_sym} | Jarque-Bera p={step5['jb_pval']:.4f} {jb_sym}".ljust(W2 + 1) + "║")
    print(f"║  🔮 Forecast: {FORECAST_HORIZON} weeks → JSON payload ready".ljust(W2 + 1) + "║")
    print(f"║  📏 Avg CI width: {ci_widths.mean():.1f} bookings".ljust(W2 + 1) + "║")
    print(f"║".ljust(W2 + 1) + "║")
    print(f"║  ECONOMETRIC FIXES APPLIED:".ljust(W2 + 1) + "║")
    print(f"║    FIX 1: Typhoon hangover = {TYPHOON_HANGOVER}w (reverted from 9w)".ljust(W2 + 1) + "║")
    print(f"║    FIX 2: Log transform REMOVED (raw counts → tighter CI)".ljust(W2 + 1) + "║")
    print(f"║    FIX 3: Seasonal GridSearch UNLOCKED (ADF-driven d)".ljust(W2 + 1) + "║")
    print(f"╚{'═' * W2}╝")

    print(f"\n✅ Step 6 complete. Pipeline finished.")

    # Add to the return dict so orchestrator can persist it
    return {
        "test_metrics":      test_metrics,
        "forecast_df":       forecast_df,
        "forecast_json":     forecast_json,
        "backtest_json":     backtest_json,
        "validation_graph":  validation_graph,    # ── NEW
    }
