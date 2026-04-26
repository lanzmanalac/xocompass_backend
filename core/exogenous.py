# ─────────────────────────────────────────────────────────────────────────────
# 1A. Philippine Holiday Engine  (UNCHANGED from v10.0)
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd


class PHHolidayEngine:
    """
    Generates daily Philippine holiday indicators.
    16 fixed-date holidays + Easter-movable holidays.
    """

    FIXED = [
        (1,  1,  "r"),   # New Year's Day
        (1,  29, "s"),   # Chinese New Year
        (2,  25, "s"),   # EDSA Revolution Anniversary
        (4,  9,  "r"),   # Araw ng Kagitingan
        (5,  1,  "r"),   # Labor Day
        (6,  12, "r"),   # Independence Day
        (8,  21, "s"),   # Ninoy Aquino Day
        (8,  26, "r"),   # National Heroes Day
        (11, 1,  "s"),   # All Saints Day
        (11, 2,  "s"),   # All Souls Day
        (11, 30, "r"),   # Bonifacio Day
        (12, 8,  "s"),   # Feast of the Immaculate Conception
        (12, 24, "s"),   # Christmas Eve
        (12, 25, "r"),   # Christmas Day
        (12, 30, "r"),   # Rizal Day
        (12, 31, "s"),   # Last Day of the Year
    ]

    MEGA_FIXED = {(12, 24), (12, 25), (12, 30), (12, 31), (1, 1), (11, 1), (11, 2)}

    @staticmethod
    def _easter(year):
        """Compute Easter Sunday using the Meeus/Jones/Butcher algorithm."""
        a = year % 19
        b, c = divmod(year, 100)
        d, e = divmod(b, 4)
        f = (b + 8) // 25
        g = (b - f + 1) // 3
        h = (19 * a + b - d - g + 15) % 30
        i, k = divmod(c, 4)
        l = (32 + 2 * e + 2 * i - h - k) % 7
        m = (a + 11 * h + 22 * l) // 451
        month = (h + l - 7 * m + 114) // 31
        day = ((h + l - 7 * m + 114) % 31) + 1
        return datetime(year, month, day)

    def generate(self, start, end):
        """Produce a DataFrame of daily holiday flags over [start, end]."""
        idx = pd.date_range(start, end, freq="D")
        df = pd.DataFrame(index=idx)
        df.index.name = "date"
        df["is_holiday"] = 0
        df["is_mega_holiday"] = 0
        df["is_long_weekend"] = 0

        for yr in range(idx.min().year, idx.max().year + 1):
            for mo, dy, ht in self.FIXED:
                try:
                    dt = pd.Timestamp(yr, mo, dy)
                    if dt in df.index:
                        df.loc[dt, "is_holiday"] = 1
                        if (mo, dy) in self.MEGA_FIXED:
                            df.loc[dt, "is_mega_holiday"] = 1
                except Exception:
                    pass

            easter = self._easter(yr)
            for offset in [-3, -2, -1]:
                dt = pd.Timestamp(easter + timedelta(days=offset))
                if dt in df.index:
                    df.loc[dt, "is_holiday"] = 1
                    df.loc[dt, "is_mega_holiday"] = 1

            black_sat = pd.Timestamp(easter + timedelta(days=-1))
            if black_sat in df.index:
                df.loc[black_sat, "is_mega_holiday"] = 1

        for dt in df.index[df["is_holiday"] == 1]:
            dow = dt.dayofweek
            if dow in [0, 4]:
                df.loc[dt, "is_long_weekend"] = 1
            elif dow == 3:
                df.loc[dt, "is_long_weekend"] = 1
                nxt = dt + timedelta(days=1)
                if nxt in df.index:
                    df.loc[nxt, "is_long_weekend"] = 1
            elif dow == 1:
                df.loc[dt, "is_long_weekend"] = 1
                prev = dt - timedelta(days=1)
                if prev in df.index:
                    df.loc[prev, "is_long_weekend"] = 1

        print(f"  🏖️ {df['is_holiday'].sum()} holiday-days | "
              f"{df['is_mega_holiday'].sum()} mega-holiday-days generated")
        return df


# ─────────────────────────────────────────────────────────────────────────────
# 1B. PAGASA Typhoon Signal Injector  (UNCHANGED from v10.0)
# ─────────────────────────────────────────────────────────────────────────────

class TyphoonInjector:
    """Injects PAGASA typhoon wind-speed signals into a daily time series."""

    STORMS: list[dict] = [
        # ── 2013 ──────────────────────────────────────────────────────────
        {"name": "GORIO",   "par_beg": "2013-06-27", "par_end": "2013-06-30", "msw": 85},
        {"name": "SANTI",   "par_beg": "2013-10-08", "par_end": "2013-10-12", "msw": 150},
        # ── 2014 ──────────────────────────────────────────────────────────
        {"name": "GLENDA",  "par_beg": "2014-07-13", "par_end": "2014-07-16", "msw": 150},
        # ── 2017 ──────────────────────────────────────────────────────────
        {"name": "MARING",  "par_beg": "2017-09-11", "par_end": "2017-09-13", "msw": 85},
        # ── 2020 ──────────────────────────────────────────────────────────
        {"name": "AMBO",    "par_beg": "2020-05-10", "par_end": "2020-05-17", "msw": 155},
        {"name": "BUTCHOY", "par_beg": "2020-06-11", "par_end": "2020-06-12", "msw": 65},
        {"name": "ULYSSES", "par_beg": "2020-11-08", "par_end": "2020-11-13", "msw": 155},
        # ── 2021 ──────────────────────────────────────────────────────────
        {"name": "JOLINA",  "par_beg": "2021-09-06", "par_end": "2021-09-09", "msw": 120},
        # ── 2022 ──────────────────────────────────────────────────────────
        {"name": "KARDING", "par_beg": "2022-09-22", "par_end": "2022-09-26", "msw": 195},
        {"name": "PAENG",   "par_beg": "2022-10-26", "par_end": "2022-10-31", "msw": 110},
        # ── 2025 ──────────────────────────────────────────────────────────
        {"name": "RAMIL",   "par_beg": "2025-10-17", "par_end": "2025-10-20", "msw": 65},
    ]

    # Gaps with no Bulacan-affecting typhoons:
    #   2015, 2016, 2018, 2019, 2023, 2024
    # These are genuine zero-typhoon years for this province, not missing data.
    # The model will correctly treat those weeks as baseline.
    _STORM_YEARS_WITH_NO_ACTIVITY = {2015, 2016, 2018, 2019, 2023, 2024}

    def generate(self, start, end) -> pd.DataFrame:
        """
        Returns a daily DataFrame with column `typhoon_msw` (float, km/h).
        Value is 0.0 on non-storm days; MSW on storm days (taking the max
        if two storms overlap, though this has not occurred in this dataset).

        Args:
            start: Any type accepted by pd.Timestamp (str, datetime, Timestamp)
            end:   Any type accepted by pd.Timestamp

        Returns:
            pd.DataFrame with DatetimeIndex named 'date' and column 'typhoon_msw'.

        Raises:
            ValueError: if start > end (nonsensical date range).
        """
        start_ts = pd.Timestamp(start)
        end_ts   = pd.Timestamp(end)

        if start_ts > end_ts:
            raise ValueError(
                f"TyphoonInjector.generate() received start={start_ts} > end={end_ts}. "
                "Verify the caller passes (series_start, series_end) in chronological order."
            )

        idx = pd.date_range(start_ts, end_ts, freq="D")
        df  = pd.DataFrame({"typhoon_msw": 0.0}, index=idx)
        df.index.name = "date"

        applied: list[str] = []
        skipped: list[str] = []  # storms outside the requested window (for auditability)

        for s in self.STORMS:
            beg   = pd.Timestamp(s["par_beg"])
            end_d = pd.Timestamp(s["par_end"])

            # Guard: storm entirely outside the requested window
            if end_d < start_ts or beg > end_ts:
                skipped.append(s["name"])
                continue

            mask = (df.index >= beg) & (df.index <= end_d)
            if mask.sum() > 0:
                # np.maximum preserves any higher MSW from an overlapping storm
                df.loc[mask, "typhoon_msw"] = np.maximum(
                    df.loc[mask, "typhoon_msw"], float(s["msw"])
                )
                applied.append(s["name"])

        n_storm_days = int((df["typhoon_msw"] > 0).sum())
        print(
            f"  🌀 {len(applied)} typhoon(s) injected over {n_storm_days} storm-days: "
            f"{', '.join(applied)}"
        )
        if skipped:
            print(
                f"     ↳ {len(skipped)} storm(s) outside window (skipped): "
                f"{', '.join(skipped)}"
            )

        return df


# ─────────────────────────────────────────────────────────────────────────────
# 1C. Weekly Holiday Intensity Classifier  (UNCHANGED from v10.0)
# ─────────────────────────────────────────────────────────────────────────────

def weekly_intensity(holidays_df, wk_index):
    """Classify each week's holiday intensity on a 0–3 ordinal scale."""
    out = []
    for wk in wk_index:
        wk_end = wk + timedelta(days=6)
        sl = holidays_df.reindex(
            pd.date_range(wk, wk_end, freq="D")
        ).fillna(0)
        if sl["is_mega_holiday"].max() > 0:
            out.append(3)
        elif sl["is_long_weekend"].max() > 0:
            out.append(2)
        elif sl["is_holiday"].sum() > 0:
            out.append(1)
        else:
            out.append(0)
    return out