###############################################################################
#                                                                             #
#   STEP 3 — STATIONARITY (The Baseline Test)                                #
#                                                                             #
#   ┌──────────────────────────────────────────────────────────────────┐      #
#   │  FIX 2: ADF test runs on raw 'y' (no log transform)            │      #
#   │  FIX 3: auto_arima now searches FULL seasonal space             │      #
#   │         (p,d,q)×(P,D,Q,52) — no locked parameters              │      #
#   └──────────────────────────────────────────────────────────────────┘      #
#                                                                             #
###############################################################################

from __future__ import annotations

import time

import numpy as np
import matplotlib.pyplot as plt
from statsmodels.tsa.stattools import acf, adfuller, pacf
from statsmodels.graphics.tsaplots import plot_acf, plot_pacf

from core.config import (
    ACF_MAX_LAG,
    ADF_ALPHA,
    GRIDSEARCH_MAX_D,
    GRIDSEARCH_MAX_DD,
    GRIDSEARCH_MAX_P,
    GRIDSEARCH_MAX_PP,
    GRIDSEARCH_MAX_Q,
    GRIDSEARCH_MAX_QQ,
    GRIDSEARCH_STEPWISE,
    MAX_D,
    SEASONAL_M,
    TEST_RATIO,
)

def _diagnose_d(series, max_d=MAX_D, alpha=ADF_ALPHA):
    """
    Determine the differencing order d via iterative ADF testing.
    H0: unit root present (non-stationary). p ≤ α → reject → stationary.
    """
    print(f"\n  ┌─ ADF Stationarity Test (α={alpha}) ─────────")
    current = series.dropna()
    for d in range(max_d + 1):
        if d > 0:
            current = series.diff(d).dropna()
        stat, p_val = adfuller(current, autolag="AIC")[:2]
        status = "✅ STATIONARY" if p_val <= alpha else "⚠️ NON-STATIONARY"
        print(f"  │  d={d}: ADF stat={stat:.4f}, p={p_val:.6f} → {status}")
        if p_val <= alpha:
            print(f"  │  → Diagnosed d = {d}")
            print(f"  └{'─' * 55}")
            return d
    print(f"  │  → Maximum d={max_d} reached")
    print(f"  └{'─' * 55}")
    return max_d


def _diagnose_pq(series, max_lag=ACF_MAX_LAG):
    """Determine AR(p) and MA(q) from PACF and ACF sharp-cutoff rule."""
    clean = series.dropna()
    n = len(clean)
    bound = 1.96 / np.sqrt(n)
    nlags = min(max_lag, n // 2 - 1)
    if nlags < 2:
        return 1, 1

    acf_vals  = acf(clean, nlags=nlags, fft=True)
    pacf_vals = pacf(clean, nlags=nlags, method="ywm")

    p = 0
    for k in range(1, len(pacf_vals)):
        if abs(pacf_vals[k]) > bound:
            p = k
        else:
            break

    q = 0
    for k in range(1, len(acf_vals)):
        if abs(acf_vals[k]) > bound:
            q = k
        else:
            break

    p = max(1, min(p, 3))
    q = max(1, min(q, 3))

    print(f"\n  ┌─ ACF/PACF Order Selection (n={n}, bound=±{bound:.4f}) ──")
    print(f"  │  PACF sharp cutoff → p = {p} (AR order)")
    print(f"  │  ACF sharp cutoff  → q = {q} (MA order)")
    print(f"  └{'─' * 55}")
    return p, q


def run_step3_stationarity(step1):
    """
    STEP 3 — STATIONARITY (The Baseline Test)

    v10.1 changes:
      FIX 2: All tests run on raw 'y' (bookings_weekly), not y_log.
      FIX 3: auto_arima now searches the FULL seasonal space:
             (p,d,q)×(P,D,Q,m=52), with d determined by internal ADF.
             The locked (1,0,0,52) seasonal constraint is REMOVED.

    Returns:
        dict with train/test splits + diagnosed orders from both methods.
    """
    print("\n╔" + "═" * 70 + "╗")
    print("║" + "  STEP 3 — STATIONARITY (The Baseline Test)".center(70) + "║")
    print("╚" + "═" * 70 + "╝")

    w        = step1["weekly_df"]
    ALL_EXOG = step1["ALL_EXOG"]

    # ═══════════════════════════════════════════════════════════════════════
    # 3.1  CHRONOLOGICAL TRAIN / TEST SPLIT
    # ═══════════════════════════════════════════════════════════════════════
    n = len(w)
    s = int(n * (1 - TEST_RATIO))
    train_wk = w.iloc[:s].copy()
    test_wk  = w.iloc[s:].copy()

    # ── FIX 2: Target is raw 'y', NOT 'y_log' ──
    train_y     = train_wk["y"].astype(float)       # <── raw counts
    test_y      = test_wk["y"].astype(float)         # <── raw counts
    train_y_raw = train_wk["bookings_weekly"].astype(float)
    test_y_raw  = test_wk["bookings_weekly"].astype(float)
    train_X     = train_wk[ALL_EXOG].astype(float)
    test_X      = test_wk[ALL_EXOG].astype(float)

    print(f"\n  📊 Split: Train = {len(train_wk)}w | Test = {len(test_wk)}w")
    print(f"     Target: 'y' (raw booking counts — FIX 2, no log)")
    print(f"     Exogenous features: {ALL_EXOG}")

    # ═══════════════════════════════════════════════════════════════════════
    # 3.2  METHOD A — STATISTICAL DIAGNOSIS (Box-Jenkins 1976)
    #      ADF → d  ;  ACF/PACF → p, q  (on raw counts)
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n{'═' * 65}")
    print(f"  METHOD A: STATISTICAL DIAGNOSIS (Box-Jenkins 1976)")
    print(f"  ⚠️  Operating on RAW COUNTS (FIX 2: no log transform)")
    print(f"{'═' * 65}")

    diag_d = _diagnose_d(train_y)
    diffed = train_y.diff(diag_d).dropna() if diag_d > 0 else train_y.dropna()
    diag_p, diag_q = _diagnose_pq(diffed)
    stat_order = (diag_p, diag_d, diag_q)
    print(f"\n  → Statistical non-seasonal order: {stat_order}")

    fig, axes = plt.subplots(1, 2, figsize=(16, 5))
    ml = min(ACF_MAX_LAG, len(diffed) // 2 - 1)
    if ml >= 2:
        plot_acf(diffed, lags=ml, ax=axes[0], alpha=0.05)
        axes[0].set_title(f"ACF after d={diag_d} (raw counts) → q={diag_q}",
                          fontweight="bold")
        plot_pacf(diffed, lags=ml, ax=axes[1], alpha=0.05, method="ywm")
        axes[1].set_title(f"PACF → p={diag_p}", fontweight="bold")
    plt.suptitle("Step 3A — Post-Differencing (Raw Counts, No Log)",
                 fontweight="bold", y=1.02)
    plt.tight_layout(); plt.show()

    # ═══════════════════════════════════════════════════════════════════════
    # 3.3  METHOD B — FULL SEASONAL GRIDSEARCH (FIX 3)
    #
    # This step originally used `pmdarima.auto_arima`. On some machines the
    # compiled pmdarima wheel can be ABI-incompatible with the installed NumPy
    # and even crash the interpreter on import. To keep the pipeline runnable,
    # we treat pmdarima as an optional accelerator:
    # - if it imports and runs, we use its AIC-optimal seasonal search
    # - otherwise we fall back to the statistical order from Method A
    # ═══════════════════════════════════════════════════════════════════════
    gs_order = None
    gs_seasonal = None
    gs_aic = None
    gs_elapsed = None

    print(f"\n{'═' * 65}")
    print(f"  METHOD B: FULL SEASONAL GRIDSEARCH (FIX 3 — UNLOCKED)")
    print(f"{'═' * 65}")
    print(f"  Search space:")
    print(f"    Non-seasonal: p ∈ [0,{GRIDSEARCH_MAX_P}], "
          f"d ∈ [0,{GRIDSEARCH_MAX_D}], q ∈ [0,{GRIDSEARCH_MAX_Q}]")
    print(f"    Seasonal:     P ∈ [0,{GRIDSEARCH_MAX_PP}], "
          f"D ∈ [0,{GRIDSEARCH_MAX_DD}], Q ∈ [0,{GRIDSEARCH_MAX_QQ}], m={SEASONAL_M}")
    print(f"    Stepwise: {GRIDSEARCH_STEPWISE}")
    print(f"    ADF-driven d: auto_arima runs its own ADF to select d")

    try:
        import pmdarima as pm  # local import: avoid hard crash at module import time

        t0_gs = time.time()
        auto_result = pm.auto_arima(
            train_y,  # <── FIX 2: raw counts, no log
            exogenous=train_X,
            # ── Non-seasonal search bounds ──
            start_p=0, max_p=GRIDSEARCH_MAX_P,
            start_q=0, max_q=GRIDSEARCH_MAX_Q,
            d=None,  # <── FIX 3: ADF determines d
            max_d=GRIDSEARCH_MAX_D,
            # ── Seasonal search bounds (FIX 3: UNLOCKED) ──
            seasonal=True,
            m=SEASONAL_M,
            start_P=0, max_P=GRIDSEARCH_MAX_PP,
            start_Q=0, max_Q=GRIDSEARCH_MAX_QQ,
            D=None,
            max_D=GRIDSEARCH_MAX_DD,
            # ── Search strategy ──
            stepwise=GRIDSEARCH_STEPWISE,
            approximation=True,
            suppress_warnings=True,
            error_action="ignore",
            information_criterion="aic",
            trace=True,
        )
        gs_elapsed = time.time() - t0_gs

        gs_order = auto_result.order
        gs_seasonal = auto_result.seasonal_order
        gs_aic = float(auto_result.aic())

        print(f"\n  → GridSearch non-seasonal order: {gs_order}")
        print(f"  → GridSearch seasonal order:     {gs_seasonal}")
        print(f"  → GridSearch AIC:                {gs_aic:.2f}")
        print(f"  → Search time:                   {gs_elapsed:.1f}s")
    except Exception as e:
        print(f"\n  ⚠️ GridSearch skipped (pmdarima unavailable/unhealthy): {e}")
        print(f"     Falling back to Method A statistical order.")

    # ═══════════════════════════════════════════════════════════════════════
    # 3.4  COMPARISON TABLE
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n{'═' * 70}")
    print(f"  ⭐ TUNING METHOD COMPARISON")
    print(f"{'═' * 70}")
    print(f"  {'Method':<35} {'Order':<12} {'Seasonal':<18} {'Basis'}")
    print(f"  {'─' * 70}")
    print(f"  {'Statistical (ADF+ACF/PACF)':<35} {str(stat_order):<12} "
          f"{'(manual — Step 5)':<18} Box-Jenkins 1976")
    print(f"  {'GridSearch (auto_arima)':<35} {str(gs_order):<12} "
          f"{str(gs_seasonal):<18} Stepwise AIC")
    print(f"  {'─' * 70}")

    if gs_order is not None and gs_seasonal is not None:
        if stat_order == gs_order:
            print(f"  ✅ Non-seasonal orders AGREE: {stat_order}")
        else:
            print(f"  ⚠️ Non-seasonal orders DISAGREE.")
            print(f"     GridSearch (AIC-optimal + seasonal-aware) will be used.")

        # GridSearch result is authoritative when available.
        final_order = gs_order
        final_seasonal = gs_seasonal
    else:
        # Conservative fallback when GridSearch is unavailable.
        final_order = stat_order
        final_seasonal = (1, 0, 0, SEASONAL_M)

    print(f"\n  → Final non-seasonal order:  {final_order}")
    print(f"  → Final seasonal order:      {final_seasonal}")
    print(f"\n✅ Step 3 complete.")

    return {
        "train_wk":       train_wk,
        "test_wk":        test_wk,
        "train_y":        train_y,        # raw counts (FIX 2)
        "test_y":         test_y,         # raw counts (FIX 2)
        "train_y_raw":    train_y_raw,
        "test_y_raw":     test_y_raw,
        "train_X":        train_X,
        "test_X":         test_X,
        "stat_order":     stat_order,
        "gs_order":       gs_order,
        "gs_seasonal":    gs_seasonal,
        "final_order":    final_order,
        "final_seasonal": final_seasonal,  # <── FIX 3: no longer locked
    }