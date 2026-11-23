"""Run the forecasting stages on a booking CSV in the browser worker."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
import csv
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import uuid4

from services.forecast_publication_views import build_forecast_publication_views


class BrowserTrainingError(Exception):
    """A training failure with a message safe to display in the browser."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _publication_snapshot(
    business_analytics: dict[str, dict], data_through: str, forecast_views: dict[str, dict]
) -> dict:
    """Identify a completed local run and assemble the approved dashboard payload."""
    run_id = uuid4()
    return {
        "schema_version": 1,
        "run_id": str(run_id),
        # The existing view contract expects a positive JavaScript-safe integer.
        "model_id": (run_id.int & ((1 << 48) - 1)) or 1,
        "run_created_at": datetime.now(timezone.utc).isoformat(),
        "data_through": data_through,
        "business_analytics": business_analytics,
        "forecast_graph": forecast_views["forecast_graph"],
        "forecast_outlook": forecast_views["forecast_outlook"],
        "strategic_actions": forecast_views["strategic_actions"],
    }


def _search_model(step1: dict, stationarity: Callable, pmdarima: Any) -> dict:
    """Apply browser-specific memory and concurrency settings to the full search."""
    import joblib

    auto_arima = pmdarima.auto_arima

    def browser_auto_arima(*args: Any, **kwargs: Any) -> Any:
        return auto_arima(*args, **{**kwargs, "n_jobs": 1, "low_memory": True})

    pmdarima.auto_arima = browser_auto_arima
    try:
        with joblib.parallel_config(backend="sequential"):
            selected = stationarity(step1)
    finally:
        pmdarima.auto_arima = auto_arima

    if selected["gs_order"] is None or selected["gs_seasonal"] is None:
        raise BrowserTrainingError("search_failed", "The full model search did not finish on this PC.")
    return selected


def train_browser_csv(
    csv_path: str | Path, report_progress: Callable[[str], None] | None = None
) -> str:
    """Return the forecast and its approved, aggregate-only publication snapshot."""
    from services.booking_analytics import BookingCSVError, prepare_browser_bookings

    try:
        with open(os.devnull, "w") as sink, redirect_stdout(sink), redirect_stderr(sink):
            bookings = prepare_browser_bookings(Path(csv_path).read_bytes())
    except BookingCSVError as exc:
        raise BrowserTrainingError("invalid_csv", str(exc)) from None
    except (csv.Error, OSError, ValueError):
        raise BrowserTrainingError("invalid_csv", "Use a valid UTF-8 CSV with consistent booking dates.") from None

    import matplotlib
    import pmdarima

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from services.pipeline.step1_ingestion import run_step1_data_ingestion
    from services.pipeline.step2_correlations import run_step2_correlations
    from services.pipeline.step3_stationarity import run_step3_stationarity
    from services.pipeline.step4_decomposition import run_step4_decomposition
    from services.pipeline.step5_training import run_step5_training
    from services.pipeline.step6_evaluation import run_step6_evaluation

    def stage(name: str, operation: Callable, *args: Any, **kwargs: Any) -> Any:
        if report_progress is not None:
            report_progress(name)
        try:
            return operation(*args, **kwargs)
        finally:
            plt.close("all")

    with TemporaryDirectory() as scratch, open(os.devnull, "w") as sink:
        with redirect_stdout(sink), redirect_stderr(sink):
            normalized = Path(scratch) / "booking-dates.csv"
            with normalized.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(["Generation Date"])
                writer.writerows((date,) for date in bookings.training_dates)

            step1 = stage("ingestion", run_step1_data_ingestion, normalized)
            stage("correlations", run_step2_correlations, step1)
            step3 = stage("model_search", _search_model, step1, run_step3_stationarity, pmdarima)
            stage("decomposition", run_step4_decomposition, step1)
            step5 = stage("fit", run_step5_training, step1, step3)
            if (step5["best_order"], step5["best_seas"]) != (step3["final_order"], step3["final_seasonal"]):
                raise BrowserTrainingError("fit_failed", "The selected model did not finish fitting on this PC.")
            step6 = stage("evaluation", run_step6_evaluation, step1, step3, step5, forecast_steps=12)

    forecast = json.loads(step6["forecast_json"])
    if len(forecast) != 12:
        raise BrowserTrainingError("training_failed", "The model did not produce a complete 12-week forecast.")
    converged = bool(step5["fitted"].mle_retvals.get("converged", False))
    snapshot = None
    if converged:
        if report_progress is not None:
            report_progress("preparing_report")
        views = build_forecast_publication_views(
            ((week.record_date, week.booking_value) for week in bookings.weekly_records),
            forecast,
            step6["validation_graph"],
            # Model weeks end on Monday. The reporting ledger labels their Tuesday start.
            validation_dates=(day - timedelta(days=6) for day in step3["test_wk"].index),
        )
        snapshot = _publication_snapshot(bookings.business_analytics, bookings.data_through, views)
    return json.dumps(
        {
            "schema_version": 1,
            "data_through": bookings.data_through,
            "bookings_used": int(step1["weekly_df"]["bookings_weekly"].sum()),
            "order": step5["best_order"],
            "seasonal_order": step5["best_seas"],
            "weekly_holdout": step6["test_metrics"]["weekly"],
            "converged": converged,
            "forecast": forecast,
            "publication_snapshot": snapshot,
        },
        allow_nan=False,
    )
