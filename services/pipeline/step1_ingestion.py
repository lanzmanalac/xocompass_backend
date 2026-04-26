# ─────────────────────────────────────────────────────────────────────────────
# 1D. Master Ingestion Function
#
#   ┌──────────────────────────────────────────────────────────────────┐
#   │  FIX 1 APPLIED HERE: TYPHOON_HANGOVER = 2 (was 9)             │
#   │  FIX 2 APPLIED HERE: log1p REMOVED — raw counts used directly │
#   └──────────────────────────────────────────────────────────────────┘
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from matplotlib.patches import Patch

from core.config import (
    FORECAST_HORIZON,
    LEAD_SEARCH_MAX,
    LEAD_SEARCH_MIN,
    SYSTEM_DATE,
    TYPHOON_HANGOVER,
    WEEKLY_RULE,
)
from core.exogenous import PHHolidayEngine, TyphoonInjector, weekly_intensity

def run_step1_data_ingestion(csv_path="KJS Data.csv"):
    """
    STEP 1 — DATA INGESTION (The Prep & Context Engine)
    """
    print("╔" + "═" * 70 + "╗")
    print("║" + "  STEP 1 — DATA INGESTION (The Prep & Context Engine)".center(70) + "║")
    print("╚" + "═" * 70 + "╝")

    # ═══════════════════════════════════════════════════════════════════════
    # 1.1  LOAD RAW CSV
    # ═══════════════════════════════════════════════════════════════════════
    df_raw = pd.read_csv(csv_path)
    print(f"\n📋 Raw data: {len(df_raw):,} transaction rows")

    booking_col = None
    for c in df_raw.columns:
        n = c.strip().lower().replace(" ", "_")
        if "generation" in n and "date" in n:
            booking_col = c; break
    if not booking_col:
        for c in df_raw.columns:
            if "travel" in c.lower() and "date" in c.lower():
                booking_col = c; break
    if not booking_col:
        for c in df_raw.columns:
            try:
                pd.to_datetime(df_raw[c].head(20)); booking_col = c; break
            except Exception:
                continue
    assert booking_col, "❌ Could not detect a date column in the CSV."
    print(f"   Date column detected: '{booking_col}'")

    df_raw["Booking_Date"] = pd.to_datetime(
        df_raw[booking_col], dayfirst=False, errors="coerce"
    )
    df_raw = df_raw.dropna(subset=["Booking_Date"])
    df_raw = df_raw[df_raw["Booking_Date"] <= SYSTEM_DATE]
    print(f"   Usable transactions:  {len(df_raw):,}")

    # ═══════════════════════════════════════════════════════════════════════
    # 1.2  DOUBLE AGGREGATION:  Raw → Daily → Weekly
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n── Double Aggregation ──")

    daily_df = (
        df_raw.groupby("Booking_Date")
        .size()
        .to_frame("bookings")
        .rename_axis("date")
        .sort_index()
    )
    full_range = pd.date_range(daily_df.index.min(), daily_df.index.max(), freq="D")
    daily_df = daily_df.reindex(full_range, fill_value=0)
    daily_df.index.name = "date"
    print(f"   Stage A (Daily):  {len(daily_df):,} days  "
          f"({daily_df.index.min().date()} → {daily_df.index.max().date()})")

    # ═══════════════════════════════════════════════════════════════════════
    # 1.3  EXOGENOUS ENGINE
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n── Exogenous Engine ──")

    extend_end = daily_df.index.max() + timedelta(
        weeks=LEAD_SEARCH_MAX + FORECAST_HORIZON + 4
    )
    holidays_df = PHHolidayEngine().generate(daily_df.index.min(), extend_end)
    typhoon_df = TyphoonInjector().generate(daily_df.index.min(), daily_df.index.max())

    # ═══════════════════════════════════════════════════════════════════════
    # 1.4  STAGE B: Weekly Aggregation + Feature Engineering
    # ═══════════════════════════════════════════════════════════════════════

    print(f"\n── Stage B: Weekly Aggregation & Feature Engineering ──")

    daily = daily_df[["bookings"]].copy()
    daily = daily.join(
        holidays_df[["is_holiday", "is_mega_holiday", "is_long_weekend"]], how="left"
    )
    daily = daily.join(typhoon_df[["typhoon_msw"]], how="left")
    for c in ["bookings", "is_holiday", "is_mega_holiday", "is_long_weekend"]:
        daily[c] = daily[c].fillna(0).astype(int)
    daily["typhoon_msw"] = daily["typhoon_msw"].fillna(0)

    w = pd.DataFrame()
    w["bookings_weekly"] = daily["bookings"].resample(WEEKLY_RULE).sum()
    w["typhoon_msw"]     = daily["typhoon_msw"].resample(WEEKLY_RULE).max()
    w["is_long_weekend"] = daily["is_long_weekend"].resample(WEEKLY_RULE).max()
    w = w.dropna()

    w["holiday_intensity"] = weekly_intensity(holidays_df, w.index)

    # ── Dynamic Holiday Lead Optimisation ──
    future_idx = pd.date_range(
        w.index.max() + timedelta(days=7),
        periods=LEAD_SEARCH_MAX + FORECAST_HORIZON + 2,
        freq=WEEKLY_RULE,
    )
    future_int = weekly_intensity(holidays_df, future_idx)
    ext = pd.Series(
        list(w["holiday_intensity"].values) + future_int,
        index=w.index.append(future_idx),
    )

    print(f"\n   ⏩ Dynamic Lead Optimisation (L={LEAD_SEARCH_MIN}..{LEAD_SEARCH_MAX})")
    lead_corrs = {}
    for L in range(LEAD_SEARCH_MIN, LEAD_SEARCH_MAX + 1):
        shifted = ext.shift(-L).reindex(w.index).fillna(0)
        r = w["bookings_weekly"].corr(shifted)
        lead_corrs[L] = r
        bar = "█" * int(max(0, r) * 50)
        print(f"      L={L:2d}: r = {r:+.4f}  {bar}")

    OPTIMAL_LEAD = max(lead_corrs, key=lead_corrs.get)
    best_r = lead_corrs[OPTIMAL_LEAD]
    print(f"      🏆 Optimal lead: L={OPTIMAL_LEAD} (r={best_r:+.4f})")

    lead_col = f"holiday_lead_{OPTIMAL_LEAD}"
    w[lead_col] = ext.shift(-OPTIMAL_LEAD).reindex(w.index).fillna(0).astype(int)

    # ══════════════════════════════════════════════════════════════════════
    # FIX 1: TYPHOON HANGOVER — 2-week rolling max (was 9)
    #
    # Econometric rationale: A typhoon is a transient exogenous shock
    # that operates on the MA (moving-average) component of the DGP.
    # Its impact on walk-in bookings decays rapidly:
    #   Week 0: Storm makes landfall → bookings suppressed
    #   Week 1: Immediate aftermath (travel advisories, road damage)
    #   Week 2+: Recovery — bookings revert to baseline
    #
    # A 2-week rolling max captures the storm week + 1 recovery week.
    # Extending beyond this introduces spurious persistence that bleeds
    # into the seasonal component and destroys exogenous significance.
    # ══════════════════════════════════════════════════════════════════════
    w["typhoon_raw"] = w["typhoon_msw"].copy()
    w["typhoon_msw"] = w["typhoon_raw"].rolling(
        window=TYPHOON_HANGOVER,   # <── FIX 1: TYPHOON_HANGOVER = 2
        min_periods=1
    ).max()
    n_storm    = (w["typhoon_raw"] > 0).sum()
    n_hangover = (w["typhoon_msw"] > 0).sum()
    n_extra    = n_hangover - n_storm
    print(f"   🌀 Typhoon hangover (FIX 1): window={TYPHOON_HANGOVER}w, "
          f"+{n_extra} lingering weeks (storm weeks={n_storm}, total={n_hangover})")

    # ══════════════════════════════════════════════════════════════════════`
    # FIX 2: LOG TRANSFORM REMOVED
    #
    # Econometric rationale: The log1p transform was applied under the
    # assumption that booking count variance grows proportionally with
    # the level (multiplicative heteroskedasticity). However, MSME
    # walk-in tourism data is:
    #   (a) Discrete count data (integer bookings/week, typically 5–50)
    #   (b) Low-variance: the coefficient of variation is moderate
    #   (c) NOT exhibiting exponential variance growth
    #
    # The log transform was HARMFUL because:
    #   1. expm1() is a convex function. By Jensen's inequality,
    #      E[expm1(ŷ)] > expm1(E[ŷ]), so the inverse transform
    #      introduces systematic upward bias in predictions.
    #   2. The 95% CI in log-space maps to ASYMMETRIC, explosively
    #      wide intervals in count-space. A symmetric ±1.0 in log-space
    #      becomes [expm1(μ-1), expm1(μ+1)] ≈ [0.37×μ, 2.72×μ],
    #      which explains the 1-to-123 CI on a 17-booking forecast.
    #   3. For count data in the range ~5–50, the raw series already
    #      has approximately constant variance, so the variance-
    #      stabilising justification for log does not apply.
    #
    # The target variable is now 'y' = bookings_weekly (raw integers).
    # All downstream code uses 'y' instead of 'y_log'.
    # No np.log1p(). No np.expm1(). No inverse transform needed.
    # ══════════════════════════════════════════════════════════════════════
    w["y"] = w["bookings_weekly"].astype(float)
    # NOTE: 'y_log' column is intentionally NOT created.
    # If any downstream code references 'y_log', it will raise a KeyError,
    # which is the desired behavior — it forces all references to be updated.

    ALL_EXOG = [lead_col, "is_long_weekend"]

    print(f"\n   📊 Weekly dataset: {len(w)} observations")
    print(f"      Mean bookings:  {w['bookings_weekly'].mean():.1f}/week")
    print(f"      Std bookings:   {w['bookings_weekly'].std():.1f}/week")
    print(f"      Target column:  'y' (raw counts, NO log transform — FIX 2)")
    print(f"      Exogenous set:  {ALL_EXOG}")
    print(f"      Date range:     {w.index.min().date()} → {w.index.max().date()}")

    # ═══════════════════════════════════════════════════════════════════════
    # 1.6  EDA VISUALISATIONS
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n── EDA Visualisations ──")

    fig, ax = plt.subplots(figsize=(15, 4))
    ax.bar(daily_df.index, daily_df["bookings"], width=1,
           color="steelblue", alpha=0.7)
    ax.set_title("Daily Booking Volume (Full History)", fontweight="bold")
    ax.set_ylabel("Bookings")
    plt.tight_layout(); plt.show()

    fig, ax = plt.subplots(figsize=(15, 4))
    cm = {0: "#2ecc71", 1: "#f39c12", 2: "#e67e22", 3: "#e74c3c"}
    ax.bar(w.index, w["bookings_weekly"], width=6,
           color=[cm[int(i)] for i in w[lead_col]], alpha=0.85)
    ax.set_title(
        f"Weekly Bookings (coloured by Lead-{OPTIMAL_LEAD} Holiday Intensity)",
        fontweight="bold",
    )
    ax.set_ylabel("Bookings/week")
    legend_patches = [
        Patch(color=cm[0], label="L0 Regular"),
        Patch(color=cm[1], label="L1 Isolated"),
        Patch(color=cm[2], label="L2 Long Weekend"),
        Patch(color=cm[3], label="L3 Mega Holiday"),
    ]
    ax.legend(handles=legend_patches, fontsize=8)
    plt.tight_layout(); plt.show()

    fig, ax1 = plt.subplots(figsize=(15, 4))
    ax1.plot(w.index, w["bookings_weekly"], color="steelblue", lw=1, label="Bookings")
    ax1.set_ylabel("Bookings/week", color="steelblue")
    ax2 = ax1.twinx()
    ax2.fill_between(w.index, 0, w["typhoon_msw"], alpha=0.3,
                     color="red", label="Typhoon MSW")
    ax2.set_ylabel("Max Sustained Wind (km/h)", color="red")
    ax1.set_title("Bookings vs Severe Weather Flags (Typhoon MSW)", fontweight="bold")
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, fontsize=8)
    plt.tight_layout(); plt.show()

    print(f"\n✅ Step 1 complete.")

    return {
        "df_raw":        df_raw,
        "daily_df":      daily_df,
        "weekly_df":     w,
        "holidays_df":   holidays_df,
        "OPTIMAL_LEAD":  OPTIMAL_LEAD,
        "lead_col":      lead_col,
        "ALL_EXOG":      ALL_EXOG,
        "revenue_total": None
    }