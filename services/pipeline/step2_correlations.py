###############################################################################
#                                                                             #
#   STEP 2 — CORRELATIONS (The Box-Jenkins Lags)                             #
#                                                                             #
#   FIX 2 impact: ACF/PACF now computed on raw 'y' instead of 'y_log'.      #
#                                                                             #
###############################################################################

from __future__ import annotations

import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from statsmodels.graphics.tsaplots import plot_acf, plot_pacf

from core.config import ACF_MAX_LAG

def run_step2_correlations(step1):
    """
    STEP 2 — CORRELATIONS (The Box-Jenkins Lags)

    v10.1: ACF/PACF computed on raw booking counts ('y'), not log-transformed.
    """
    print("\n╔" + "═" * 70 + "╗")
    print("║" + "  STEP 2 — CORRELATIONS (The Box-Jenkins Lags)".center(70) + "║")
    print("╚" + "═" * 70 + "╝")

    w        = step1["weekly_df"]
    ALL_EXOG = step1["ALL_EXOG"]

    # ═══════════════════════════════════════════════════════════════════════
    # 2.1  PEARSON CORRELATION MATRIX
    # ═══════════════════════════════════════════════════════════════════════
    corr_cols = ["bookings_weekly"] + ALL_EXOG + ["holiday_intensity", "typhoon_msw"]
    corr_cols = [c for c in corr_cols if c in w.columns]
    corr_matrix = w[corr_cols].corr(method="pearson")

    target_corr = (
        corr_matrix["bookings_weekly"]
        .drop("bookings_weekly")
        .sort_values(key=abs, ascending=False)
    )

    print(f"\n  Pearson r with bookings_weekly:")
    print(f"  {'─' * 55}")
    for var, r in target_corr.items():
        sig = "⭐" if abs(r) > 0.15 else "  "
        if r is None or (isinstance(r, float) and (r != r)):  # NaN check
            bar = "░" * 40  # or skip, or use 0
        else:
            bar = "█" * int(abs(r) * 40)
        print(f"  {sig} {var:25s}  r = {r:+.4f}  {'+'if r > 0 else '-'}{bar}")
    print(f"  {'─' * 55}")

    fig, axes = plt.subplots(1, 2, figsize=(16, 6))
    mask = np.triu(np.ones_like(corr_matrix, dtype=bool), k=1)
    sns.heatmap(corr_matrix, mask=mask, annot=True, fmt=".3f", cmap="RdBu_r",
                center=0, vmin=-1, vmax=1, square=True, linewidths=0.5,
                cbar_kws={"shrink": 0.8}, ax=axes[0])
    axes[0].set_title("Correlation Matrix", fontweight="bold")

    colors = ["#2ecc71" if r > 0 else "#e74c3c" for r in target_corr.values]
    axes[1].barh(range(len(target_corr)), target_corr.values, color=colors, alpha=0.8)
    axes[1].set_yticks(range(len(target_corr)))
    axes[1].set_yticklabels(target_corr.index, fontsize=9)
    axes[1].set_xlabel("Pearson r")
    axes[1].axvline(0, color="black", lw=0.8)
    axes[1].set_title("Correlation with bookings_weekly", fontweight="bold")
    axes[1].invert_yaxis()
    plt.tight_layout(); plt.show()

    # ═══════════════════════════════════════════════════════════════════════
    # 2.2  PRELIMINARY ACF / PACF — on raw 'y' (FIX 2: no log)
    # ═══════════════════════════════════════════════════════════════════════
    series = w["y"].dropna()   # <── FIX 2: was w["y_log"]
    ml = min(ACF_MAX_LAG, len(series) // 2 - 1)

    if ml >= 2:
        fig, axes = plt.subplots(1, 2, figsize=(16, 5))
        plot_acf(series, lags=ml, ax=axes[0], alpha=0.05)
        axes[0].set_title("ACF of y (raw counts, pre-differencing)", fontweight="bold")
        plot_pacf(series, lags=ml, ax=axes[1], alpha=0.05, method="ywm")
        axes[1].set_title("PACF of y (raw counts, pre-differencing)", fontweight="bold")
        plt.suptitle("Step 2 — Preliminary Lag Structure (No Log Transform)",
                     fontweight="bold", y=1.02)
        plt.tight_layout(); plt.show()
# ── NEW: Format the correlations for the database & frontend API ──
    correlation_heatmap = []
    for var, r in target_corr.items():
        # Skip NaNs
        if r is not None and not (isinstance(r, float) and (r != r)):
            correlation_heatmap.append({
                "variable": str(var),
                "correlation": round(float(r), 4)
            })

    print(f"\nStep 2 complete.")
    return {
        "corr_matrix": corr_matrix,
        "correlation_heatmap": correlation_heatmap # <-- Added this!
    }