"""Build JSON-ready Forecast Actions views from aggregate weekly observations."""

from __future__ import annotations

from calendar import month_abbr
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class ForecastWeek:
    forecast_date: datetime
    predicted: float | None
    lower_bound: float | None
    upper_bound: float | None
    confidence_tier: str | None = None
    risk_flag: str | None = None


def classify_risk(ci_gap: float) -> str:
    """Map the gap between interval half-width and predicted bookings to a tier."""
    ci_gap = float(ci_gap)
    if ci_gap <= 4.0:
        return "Low"
    if ci_gap <= 5.5:
        return "Medium"
    if ci_gap <= 6.5:
        return "High"
    return "Critical"


def index_validation_rows(
    rows: Iterable[Mapping[str, Any]], *, dates: Iterable[date] | None = None,
) -> dict[date, Mapping[str, Any]]:
    """Index holdout predictions by week, recovering legacy dates from labels."""
    if dates is not None:
        return {
            day.date() if isinstance(day, datetime) else day: row
            for day, row in zip(dates, rows, strict=True)
        }

    indexed = {}
    for row in rows:
        try:
            month, week, year = row["date_label"].split()
            first_day = date(int(year), list(month_abbr).index(month), 1)
            monday = first_day + timedelta(days=(-first_day.weekday()) % 7)
            indexed[monday + timedelta(weeks=int(week[1:]) - 1)] = row
        except (AttributeError, KeyError, OverflowError, TypeError, ValueError):
            continue
    return indexed


def _graph_point(
    timestamp: datetime,
    actual: float | None,
    predicted: float | None,
    lower: float | None,
    upper: float | None,
    tier: str | None,
    *,
    round_validation: bool = False,
) -> dict[str, object]:
    ci_ratio = ci_gap = risk = None
    if predicted is not None and lower is not None and upper is not None:
        ci_ratio = (upper - lower) / 2.0
        ci_gap = ci_ratio - predicted
        risk = classify_risk(ci_gap)

    if round_validation:
        predicted, lower, upper = (
            round(value, 2) if value is not None else None for value in (predicted, lower, upper)
        )
    return {
        "date": timestamp.isoformat(),
        "actual": float(actual) if actual is not None else None,
        "predicted": predicted,
        "lower_bound": lower,
        "upper_bound": upper,
        "confidence_tier": tier,
        "ci_ratio": round(ci_ratio, 4) if ci_ratio is not None else None,
        "ci_gap": round(ci_gap, 4) if ci_gap is not None else None,
        "risk_factor": risk,
    }


def build_forecast_graph(
    weekly_actuals: Iterable[tuple[datetime, float | None]],
    forward_weeks: Iterable[ForecastWeek],
    validation: Mapping[date, Mapping[str, Any]],
) -> dict[str, object]:
    points = []
    actual_dates = set()
    for timestamp, actual in weekly_actuals:
        actual_day = timestamp.date()
        actual_dates.add(actual_day)
        overlay = validation.get(actual_day, {})
        predicted, lower, upper = (
            float(overlay[key]) if overlay.get(key) is not None else None
            for key in ("forecasted", "lower_ci", "upper_ci")
        )
        points.append(_graph_point(
            timestamp, actual, predicted, lower, upper,
            "BACKTEST" if overlay else None, round_validation=True,
        ))

    points.extend(
        _graph_point(
            week.forecast_date, None, week.predicted, week.lower_bound, week.upper_bound,
            week.confidence_tier,
        )
        for week in forward_weeks
        if week.forecast_date.date() not in actual_dates
    )
    return {"data": points}


def build_forecast_outlook(weeks: Sequence[ForecastWeek]) -> dict[str, object]:
    values = [week.predicted or 0.0 for week in weeks]
    peak = max(range(len(weeks)), key=values.__getitem__)
    return {
        "forecasted_bookings_2w": int(sum(values[:2])),
        "highest_forecast_week_date": weeks[peak].forecast_date.isoformat(),
        "highest_forecast_week_value": int(values[peak]),
        "forecasted_bookings_10w": int(sum(values[:10])),
        "critical_weeks": [
            {
                "week_start": week.forecast_date.isoformat(),
                "week_end": (week.forecast_date + timedelta(days=6)).isoformat(),
                "forecasted_volume": int(value),
                "risk_factor": week.risk_flag if week.risk_flag in ("HIGH", "MEDIUM", "LOW") else "MEDIUM",
                "confidence_tier": week.confidence_tier or None,
            }
            for week, value in zip(weeks, values)
        ],
    }


def build_strategic_actions(weeks: Sequence[ForecastWeek]) -> dict[str, object]:
    values = [week.predicted or 0.0 for week in weeks]
    peak = max(values)
    trough = min(values)
    average = sum(values) / len(values)
    actions = []
    if peak > average * 1.3:
        peak_month = weeks[values.index(peak)].forecast_date.strftime("%B %Y")
        actions.extend((
            {
                "priority": "HIGH",
                "category": "Staffing",
                "action": "Increase booking staff by 30% during peak demand window.",
                "trigger": f"Forecast peaks at {peak:.0f} bookings — 30% above average near {peak_month}.",
            },
            {
                "priority": "HIGH",
                "category": "Pricing",
                "action": "Apply premium pricing tier during peak weeks to maximize revenue per booking.",
                "trigger": f"Demand surge detected above {average * 1.3:.0f} bookings/week threshold.",
            },
        ))
    if trough < average * 0.7:
        actions.append({
            "priority": "MEDIUM",
            "category": "Marketing",
            "action": "Launch promotional packages 6 weeks before forecasted low-demand period.",
            "trigger": f"Demand trough of {trough:.0f} bookings forecasted — 30% below average.",
        })
    if values[-1] > values[0]:
        actions.append({
            "priority": "LOW",
            "category": "Pricing",
            "action": "Review base pricing quarterly — upward booking trend supports gradual rate increases.",
            "trigger": f"{len(weeks)}-week forecast shows positive demand trajectory.",
        })

    return {
        "actions": actions,
        "generated_for_period": (
            f"{weeks[0].forecast_date:%B %Y} – {weeks[-1].forecast_date:%B %Y}"
        ),
    }


def build_forecast_publication_views(
    weekly_actuals: Iterable[tuple[date, float]],
    forecast_rows: Iterable[Mapping[str, Any]],
    validation_rows: Iterable[Mapping[str, Any]],
    *,
    validation_dates: Iterable[date] | None = None,
) -> dict[str, dict[str, object]]:
    """Compose the browser snapshot's forecast views from sorted pipeline outputs."""
    manila = ZoneInfo("Asia/Manila")

    def localize(value: date) -> datetime:
        timestamp = value if isinstance(value, datetime) else datetime.combine(value, time.min)
        if timestamp.tzinfo is None:
            return timestamp.replace(tzinfo=manila)
        return timestamp.astimezone(manila)

    actuals = ((localize(day), count) for day, count in weekly_actuals)
    forecast_weeks = [
        ForecastWeek(
            forecast_date=localize(date.fromisoformat(row["week_start"])),
            predicted=float(row["forecast_bookings"]),
            lower_bound=float(row["confidence_lower_95"]),
            upper_bound=float(row["confidence_upper_95"]),
            confidence_tier=row["confidence_tier"],
        )
        for row in forecast_rows
    ]
    return {
        "forecast_graph": build_forecast_graph(
            actuals, forecast_weeks, index_validation_rows(validation_rows, dates=validation_dates),
        ),
        "forecast_outlook": build_forecast_outlook(forecast_weeks),
        "strategic_actions": build_strategic_actions(forecast_weeks),
    }
