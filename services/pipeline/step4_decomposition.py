###############################################################################
#                                                                             #
#   STEP 4 — DECOMPOSITION (The Seasonal Proof)                              #
#                                                                             #
#   FIX 2 impact: STL runs on raw 'y' instead of 'y_log'.                  #
#                                                                             #
###############################################################################

from __future__ import annotations

import numpy as np
import matplotlib.pyplot as plt
from statsmodels.tsa.seasonal import STL

def run_step4_decomposition(step1):
    """
    STEP 4 — DECOMPOSITION (The Seasonal Proof)

    v10.1: STL operates on raw booking counts ('y'), not log-transformed.
    """
    print("\n╔" + "═" * 70 + "╗")
    print("║" + "  STEP 4 — DECOMPOSITION (The Seasonal Proof)".center(70) + "║")
    print("╚" + "═" * 70 + "╝")

    series = step1["weekly_df"]["y"]   # <── FIX 2: was "y_log"
    stl_results = {}

    for period, label in [(4, "Monthly Cycle (m=4)"), (13, "Quarterly Cycle (m=13)")]:
        clean = series.dropna()
        n = len(clean)

        if n < period * 2 + 1:
            print(f"  ⚠️ STL m={period}: insufficient data ({n} < {period*2+1})")
            continue

        stl = STL(clean, period=period, robust=True).fit()

        var_r  = np.var(stl.resid)
        var_sr = np.var(stl.seasonal + stl.resid)
        var_tr = np.var(stl.trend + stl.resid)
        F_s = max(0, 1 - var_r / var_sr) if var_sr > 0 else 0
        F_t = max(0, 1 - var_r / var_tr) if var_tr > 0 else 0

        print(f"\n  {'═' * 65}")
        print(f"  📊 STL — {label} (n={n}) [raw counts — FIX 2]")
        print(f"  {'═' * 65}")
        print(f"  {'Component':<12} {'Mean':>10} {'Std':>10} {'Min':>10} {'Max':>10}")
        print(f"  {'─' * 54}")
        for nm, comp in [("Trend", stl.trend), ("Seasonal", stl.seasonal),
                         ("Residual", stl.resid)]:
            print(f"  {nm:<12} {comp.mean():>10.2f} {comp.std():>10.2f} "
                  f"{comp.min():>10.2f} {comp.max():>10.2f}")
        print()
        print(f"  F_s (seasonal) = {F_s:.4f}  "
              f"{'✅ strong (>0.64)' if F_s > 0.64 else '❌ weak (≤0.64)'}")
        print(f"  F_t (trend)    = {F_t:.4f}  "
              f"{'✅ strong (>0.64)' if F_t > 0.64 else '❌ weak (≤0.64)'}")

        if F_s <= 0.64:
            print(f"\n  📝 Seasonal component alone is WEAK. The residual contains")
            print(f"     demand shocks (holidays, typhoons) that exogenous variables")
            print(f"     in SARIMAX are designed to capture.")

        fig, axes = plt.subplots(4, 1, figsize=(16, 12), sharex=True)
        axes[0].plot(clean.index, clean.values, lw=0.8, color="steelblue")
        axes[0].set_title("Observed (raw counts)", fontweight="bold")
        axes[0].set_ylabel("Bookings/week")
        axes[1].plot(clean.index, stl.trend, lw=1.5, color="#e74c3c")
        axes[1].set_title(f"Trend (F_t = {F_t:.4f})", fontweight="bold")
        axes[2].plot(clean.index, stl.seasonal, lw=0.8, color="#2ecc71")
        axes[2].fill_between(clean.index, 0, stl.seasonal, alpha=0.3, color="#2ecc71")
        axes[2].axhline(0, color="gray", ls="--", lw=0.5)
        axes[2].set_title(f"Seasonal (F_s = {F_s:.4f})", fontweight="bold")
        axes[3].scatter(clean.index, stl.resid, s=8, alpha=0.6, color="#9b59b6")
        axes[3].axhline(0, color="red", ls="--", lw=0.8)
        axes[3].set_title("Residual", fontweight="bold")
        plt.suptitle(f"STL Decomposition — {label} (Raw Counts)", fontweight="bold", y=1.01)
        plt.tight_layout(); plt.show()

        stl_results[period] = {"F_s": F_s, "F_t": F_t, "label": label}

    if len(stl_results) >= 2:
        print(f"\n  ┌─ STL COMPARISON ─────────────────────")
        print(f"  │  {'Period':<10} {'F_s':>8} {'F_t':>8}")
        print(f"  │  {'─' * 30}")
        for per, r in stl_results.items():
            print(f"  │  m={per:<6} {r['F_s']:>8.4f} {r['F_t']:>8.4f}")
        print(f"  └{'─' * 35}")

    print(f"\n✅ Step 4 complete.")
    return {"stl_results": stl_results}