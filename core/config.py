"""
Global pipeline configuration for XoCompass.

This file is intentionally a plain Python module so pipeline steps can do:
`from core.config import ...`

Do NOT add notebook/Colab-only syntax here (e.g. `!pip install`).
"""

from __future__ import annotations

import pandas as pd

# ── Temporal parameters ──
WEEKLY_RULE = "W-MON"  # Non-overlapping weekly buckets (Monday start)
FORECAST_HORIZON = 16  # Weeks to forecast into the future
TEST_RATIO = 0.20  # 20% chronological holdout for evaluation
SYSTEM_DATE = pd.Timestamp("2025-12-29")  # Last allowable date in data

# ── Holiday Lead Search ──
LEAD_SEARCH_MIN = 1
LEAD_SEARCH_MAX = 8

# ── FIX 1: Typhoon hangover ──
TYPHOON_HANGOVER = 2
WEATHER_HORIZON = 2

# ── Stationarity Testing ──
ADF_ALPHA = 0.05  # Significance level for ADF test
ACF_MAX_LAG = 10  # Maximum lags to inspect in ACF/PACF
MAX_D = 2  # Maximum differencing order to try

# ── FIX 3: SARIMAX Search Space (UNLOCKED) ──────────────────────────────
# The seasonal order is NO LONGER locked to (1,0,0,52). pmdarima
# auto_arima will sweep the full search space defined below.
# The seasonal period m=52 is retained because tourism demand is
# inherently annual (52 weeks = 1 year).
SEASONAL_M = 52  # Annual cycle (weeks)
GRIDSEARCH_MAX_P = 2  # Max non-seasonal AR order
GRIDSEARCH_MAX_D = 1  # Max non-seasonal differencing
GRIDSEARCH_MAX_Q = 2  # Max non-seasonal MA order
GRIDSEARCH_MAX_PP = 1  # Max seasonal AR order
GRIDSEARCH_MAX_DD = 1  # Max seasonal differencing
GRIDSEARCH_MAX_QQ = 1  # Max seasonal MA order
GRIDSEARCH_STEPWISE = False 