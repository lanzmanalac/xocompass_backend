###############################################################################
#                                                                             #
#   STEP 5 — TRAINING (The Core Engine)                                      #
#                                                                             #
#   ┌──────────────────────────────────────────────────────────────────┐      #
#   │  FIX 2: Model trains on raw 'y' — no log/exp transforms        │      #
#   │  FIX 3: Seasonal order comes from auto_arima (NOT locked)       │      #
#   │         Predictions are direct booking counts (no expm1)        │      #
#   └──────────────────────────────────────────────────────────────────┘      #
#                                                                             #
###############################################################################

from __future__ import annotations

import time

import numpy as np
import matplotlib.pyplot as plt
from scipy import stats as sp_stats
from statsmodels.graphics.tsaplots import plot_acf
from statsmodels.stats.diagnostic import acorr_ljungbox
from statsmodels.stats.stattools import jarque_bera
from statsmodels.tsa.statespace.sarimax import SARIMAX

def run_step5_training(step1, step3):
    """
    STEP 5 — TRAINING (The Core Engine)

    v10.1 changes:
      FIX 2: Target is raw booking counts. No log1p applied before
             training, no expm1 needed after prediction. Predictions
             are directly in booking-count units.
      FIX 3: Seasonal order is the auto_arima result from Step 3
             (no longer locked to (1,0,0,52)).

    The model is re-fitted via statsmodels SARIMAX (not pmdarima's
    internal model) to ensure full access to diagnostics, .summary(),
    .get_forecast(), and confidence interval machinery.
    """
    print("\n╔" + "═" * 70 + "╗")
    print("║" + "  STEP 5 — TRAINING (The Core Engine)".center(70) + "║")
    print("╚" + "═" * 70 + "╝")

    train_y  = step3["train_y"]        # <── FIX 2: raw counts
    train_X  = step3["train_X"]
    
    if train_X is not None and train_X.empty: train_X = None

    order    = step3["final_order"]     # <── FIX 3: from auto_arima
    seasonal = step3["final_seasonal"]  # <── FIX 3: from auto_arima
    lead_col = step1["lead_col"]
    OPTIMAL_LEAD = step1["OPTIMAL_LEAD"]

    print(f"\n  Non-seasonal order (auto_arima): {order}    (FIX 3: ADF-driven d)")
    print(f"  Seasonal order (auto_arima):     {seasonal} (FIX 3: UNLOCKED)")
    print(f"  Target variable:                 'y' (raw counts, FIX 2: no log)")
    print(f"  Exogenous features:              {step1['ALL_EXOG']}")
    print(f"  Training observations:           {len(train_y)}")

    # ═══════════════════════════════════════════════════════════════════════
    # 5.1  FIT SARIMAX MODEL
    #
    # We re-fit through statsmodels.SARIMAX (rather than using pmdarima's
    # .arima_res_ directly) because statsmodels gives us:
    #   - .summary() with full coefficient table and p-values
    #   - .get_forecast() with properly computed confidence intervals
    #   - .resid for Ljung-Box and Jarque-Bera diagnostics
    #
    # The orders come from pmdarima's AIC-optimal search (FIX 3).
    # ═══════════════════════════════════════════════════════════════════════
    t0 = time.time()
    try:
        sm = SARIMAX(
            train_y,                        # <── FIX 2: raw counts
            exog=train_X,
            order=order,                    # <── FIX 3: auto_arima result
            seasonal_order=seasonal,        # <── FIX 3: auto_arima result
            enforce_stationarity=False,
            enforce_invertibility=False,
        ).fit(disp=False, maxiter=500)
    except Exception as e:
        print(f"  ⚠️ Convergence issue with {order}×{seasonal}: {e}")
        fallback_order = (1, order[1], 1)
        print(f"  🔄 Falling back to {fallback_order}×{seasonal}")
        sm = SARIMAX(
            train_y, exog=train_X,
            order=fallback_order,
            seasonal_order=seasonal,
            enforce_stationarity=False,
            enforce_invertibility=False,
        ).fit(disp=False, maxiter=500)
        order = fallback_order

    elapsed = time.time() - t0
    print(f"\n  ✅ Model fitted in {elapsed:.1f}s")
    print(f"  AIC = {sm.aic:.2f}")

    # ═══════════════════════════════════════════════════════════════════════
    # 5.2  MODEL SUMMARY
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n{sm.summary()}")

    # ═══════════════════════════════════════════════════════════════════════
    # 5.3  RESIDUAL DIAGNOSTICS
    # ═══════════════════════════════════════════════════════════════════════
    res = sm.resid

    fig, axes = plt.subplots(2, 2, figsize=(15, 10))
    axes[0, 0].plot(res, lw=0.8, color="steelblue")
    axes[0, 0].axhline(0, color="red", ls="--")
    axes[0, 0].set_title("Residuals Over Time", fontweight="bold")

    axes[0, 1].hist(res, bins=20, edgecolor="white", color="steelblue", density=True)
    axes[0, 1].set_title("Residual Distribution", fontweight="bold")

    sp_stats.probplot(res.dropna(), dist="norm", plot=axes[1, 0])
    axes[1, 0].set_title("Normal Q-Q Plot", fontweight="bold")

    nl = min(20, len(res) // 2 - 1)
    if nl > 1:
        plot_acf(res.dropna(), lags=nl, ax=axes[1, 1], alpha=0.05)
    axes[1, 1].set_title("ACF of Residuals", fontweight="bold")

    plt.suptitle(f"Step 5 — Residual Diagnostics (Raw Counts)", fontweight="bold", y=1.01)
    plt.tight_layout(); plt.show()

    lb_p = np.nan
    lbs = [l for l in [5, 10] if l < len(res) // 2]
    if lbs:
        lb_df = acorr_ljungbox(res.dropna(), lags=lbs, return_df=True)
        lb_p = lb_df["lb_pvalue"].iloc[-1]
        print(f"\n  Ljung-Box Test (H0: residuals are white noise):")
        print(f"  {lb_df.to_string()}")

    jbs, jbp = jarque_bera(res.dropna())[:2]
    print(f"\n  Jarque-Bera Test (H0: residuals are normal):")
    print(f"    Statistic = {jbs:.4f}, p-value = {jbp:.4f}")

    lb_ok = "✅ White noise" if lb_p > 0.05 else "⚠️ Autocorrelation detected"
    jb_ok = "✅ Normal" if jbp > 0.05 else "⚠️ Non-normal"
    print(f"\n  Ljung-Box:   p = {lb_p:.4f} → {lb_ok}")
    print(f"  Jarque-Bera: p = {jbp:.4f} → {jb_ok}")

    # ═══════════════════════════════════════════════════════════════════════
    # 5.4  COEFFICIENT INTERPRETATION
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n{'═' * 70}")
    print(f"  📝 COEFFICIENT INTERPRETATION")
    print(f"{'═' * 70}")
    # ── FIX 2 impact on interpretation ──
    # Because we removed the log transform, coefficients are now in
    # NATURAL UNITS (bookings/week), not log-scale. This means:
    #   coef = +5.0 → the feature adds ~5 bookings/week
    #   coef = -3.2 → the feature removes ~3 bookings/week
    # This is far more interpretable for an MSME operator than the
    # previous "percentage change via expm1(coef)" interpretation.
    print(f"  Coefficients are in NATURAL UNITS (bookings/week).")
    print(f"  coef = +5.0 means the feature adds ~5 bookings/week.")
    print(f"  P>|z| < 0.05 = statistically significant at 95% confidence.\n")

    if hasattr(sm, "params"):
        params = sm.params
        pv = sm.pvalues

        sig_count = 0
        print("  SIGNIFICANT (p < 0.05):")
        for v in params.index:
            if pv[v] < 0.05:
                d = "↑" if params[v] > 0 else "↓"
                print(f"    {d} {v:30s} coef={params[v]:+.4f}  p={pv[v]:.4f}")
                sig_count += 1

        print(f"\n  NON-SIGNIFICANT (p ≥ 0.05):")
        for v in params.index:
            if pv[v] >= 0.05:
                print(f"    • {v:30s} coef={params[v]:+.4f}  p={pv[v]:.4f}")

        if lead_col in params.index:
            hc = params[lead_col]
            hp = pv[lead_col]
            print(f"\n  🏖️ HOLIDAY LEAD-{OPTIMAL_LEAD}: coef = {hc:+.4f}, p = {hp:.4f}")
            if hp < 0.05 and hc > 0:
                # FIX 2: coefficient is now in booking units directly
                print(f"     Each +1 intensity level {OPTIMAL_LEAD} weeks ahead")
                print(f"     adds ~{hc:+.1f} bookings/week to the forecast.")
            elif hp >= 0.05:
                print(f"     ⚠️ Not statistically significant at α=0.05.")

        print(f"\n  📊 Total significant coefficients: {sig_count}/{len(params)}")

    print(f"\n✅ Step 5 complete.")

    return {
        "fitted":     sm,
        "best_order": order,
        "best_seas":  seasonal,
        "lb_pval":    lb_p,
        "jb_pval":    jbp,
    }