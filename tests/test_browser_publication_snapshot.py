from datetime import date, datetime, timedelta, timezone
import json
from unittest import TestCase

from api.schemas import PublicationSnapshot
from services.pipeline import browser_training
from tests.test_hosted_viewer import synthetic_publication


class BrowserPublicationSnapshotTests(TestCase):
    def setUp(self):
        self.dashboard = synthetic_publication()["snapshot"]
        self.forecast_views = {
            key: self.dashboard[key]
            for key in ("forecast_graph", "forecast_outlook", "strategic_actions")
        }

    def build_snapshot(self):
        return browser_training._publication_snapshot(
            self.dashboard["business_analytics"],
            self.dashboard["data_through"],
            self.forecast_views,
        )

    def test_snapshot_is_ready_for_the_hosted_publication_contract(self):
        snapshot = self.build_snapshot()
        PublicationSnapshot.model_validate_json(json.dumps(snapshot), strict=True, extra="forbid")
        self.assertEqual(snapshot["business_analytics"], self.dashboard["business_analytics"])
        self.assertEqual(snapshot["data_through"], self.dashboard["data_through"])
        self.assertGreater(snapshot["model_id"], 0)
        self.assertLessEqual(snapshot["model_id"], (1 << 53) - 1)
        self.assertEqual(
            datetime.fromisoformat(snapshot["run_created_at"].replace("Z", "+00:00")).tzinfo,
            timezone.utc,
        )

    def test_two_training_runs_have_distinct_publication_ids(self):
        self.assertNotEqual(self.build_snapshot()["run_id"], self.build_snapshot()["run_id"])

    def test_local_booking_report_has_complete_views_without_individual_records(self):
        from services.booking_analytics import prepare_browser_bookings
        from services.forecast_publication_views import build_forecast_publication_views

        bookings = prepare_browser_bookings(
            b"Generation Date,Net Amount,Booking Reference\n"
            b"2025-01-04,50,PRIVATE-ROW-X\n"
            b"2025-01-05,75,PRIVATE-ROW-Y\n"
        )
        forecast = [
            {
                "week_start": (date(2025, 1, 13) + timedelta(weeks=index)).isoformat(),
                "forecast_bookings": 3,
                "confidence_lower_95": 1,
                "confidence_upper_95": 5,
                "confidence_tier": "LOWER",
            }
            for index in range(12)
        ]
        views = build_forecast_publication_views(
            ((week.record_date, week.booking_value) for week in bookings.weekly_records),
            forecast,
            [],
        )
        snapshot = browser_training._publication_snapshot(bookings.business_analytics, bookings.data_through, views)
        payload = json.dumps(snapshot, allow_nan=False)
        PublicationSnapshot.model_validate_json(payload, strict=True, extra="forbid")
        self.assertNotIn("PRIVATE-ROW", payload)
        self.assertEqual(snapshot["business_analytics"]["overall"]["total_revenue"], 125.0)
        self.assertEqual(snapshot["business_analytics"]["overall"]["total_transaction_count"], 2)
        self.assertEqual(snapshot["forecast_outlook"]["forecasted_bookings_2w"], 6)
        self.assertEqual(len(snapshot["forecast_outlook"]["critical_weeks"]), 12)
        self.assertEqual(snapshot["data_through"], "2025-01-05")
