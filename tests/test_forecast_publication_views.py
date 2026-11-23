import json
from datetime import date, datetime, timedelta
from unittest import TestCase

from api.schemas import (
    ForecastGraphResponse,
    ForecastOutlookResponse,
    StrategicActionsResponse,
)


class ForecastPublicationViewTests(TestCase):
    def test_browser_outlook_and_actions_use_forward_forecast(self):
        from services.forecast_publication_views import build_forecast_publication_views

        actuals = [(date(2026, 8, 3), 4), (date(2026, 8, 10), 7)]
        forecast_start = date(2026, 8, 17)
        rows = [
            {
                "week_start": (forecast_start + timedelta(weeks=index)).isoformat(),
                "forecast_bookings": value,
                "confidence_lower_95": 0,
                "confidence_upper_95": 30,
                "confidence_tier": "HIGH" if index < 2 else "LOWER",
                "raw_csv": "synthetic private field",
            }
            for index, value in enumerate((5, *([10] * 10), 30))
        ]
        validation = [{
            "date_label": "Aug W2 2026", "actual": 5, "forecasted": 6.25,
            "lower_ci": 2.00, "upper_ci": 9.25,
            "passenger": "synthetic private field",
        }]

        views = build_forecast_publication_views(
            actuals, rows, validation, validation_dates=[actuals[0][0]],
        )

        payload = json.dumps(views, allow_nan=False)
        self.assertNotIn("synthetic private", payload)
        graph = ForecastGraphResponse.model_validate(views["forecast_graph"]).data
        outlook = ForecastOutlookResponse.model_validate(views["forecast_outlook"])
        actions = StrategicActionsResponse.model_validate(views["strategic_actions"])
        self.assertEqual(len(graph), 14)
        self.assertEqual(graph[0].actual, 4)
        self.assertEqual(graph[0].predicted, 6.25)
        self.assertEqual(graph[0].confidence_tier, "BACKTEST")
        self.assertIsNone(graph[1].predicted)
        self.assertIsNone(graph[2].actual)
        self.assertEqual(graph[2].date.date(), forecast_start)
        self.assertEqual(graph[2].risk_factor, "Critical")
        self.assertEqual(graph[2].ci_ratio, 15.0)
        self.assertEqual(graph[2].ci_gap, 10.0)
        self.assertEqual(outlook.forecasted_bookings_2w, 15)
        self.assertEqual(outlook.forecasted_bookings_10w, 95)
        self.assertEqual(outlook.highest_forecast_week_value, 30)
        self.assertEqual(len(outlook.critical_weeks), 12)
        self.assertEqual(outlook.critical_weeks[0].week_end.date(), forecast_start + timedelta(days=6))
        self.assertEqual(outlook.critical_weeks[0].risk_factor, "MEDIUM")
        self.assertEqual([action.category for action in actions.actions], ["Staffing", "Pricing", "Marketing", "Pricing"])
        self.assertIn("12-week forecast", actions.actions[-1].trigger)

    def test_native_graph_keeps_legacy_overlay_and_skips_duplicate_actual_dates(self):
        from services.forecast_publication_views import (
            ForecastWeek,
            build_forecast_graph,
            index_validation_rows,
        )

        actual_date = datetime(2026, 8, 10)
        forward_date = datetime(2026, 8, 17)
        overlay = index_validation_rows([
            {"date_label": "missing"},
            {"date_label": "Aug W2 2026", "forecasted": 7.3125, "lower_ci": 1.0, "upper_ci": 10.0},
        ])
        graph = build_forecast_graph(
            [(actual_date, 6)],
            [
                ForecastWeek(actual_date, 99, 0, 100, "HIGH"),
                ForecastWeek(forward_date, 10, 0, 30, "LOWER"),
            ],
            overlay,
        )

        self.assertEqual(len(graph["data"]), 2)
        self.assertEqual(graph["data"][0]["date"], "2026-08-10T00:00:00")
        self.assertEqual(graph["data"][0]["predicted"], 7.31)
        self.assertEqual(graph["data"][0]["ci_gap"], -2.8125)
        self.assertEqual(graph["data"][0]["risk_factor"], "Low")
        self.assertEqual(graph["data"][1]["risk_factor"], "Medium")
