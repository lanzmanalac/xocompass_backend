from datetime import date, datetime
from unittest import TestCase
from unittest.mock import patch

import pandas as pd
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from api.schemas import BusinessAnalyticsResponse
from core.config import MANILA_TIMEZONE
from domain.models import Base, DatasetSnapshot, TrainingDataLog
from services.booking_analytics import business_analytics_view, prepare_browser_bookings
from services.ingestion_service import ingest_csv


CSV = (
    b"Booking Date,Travel Date,Airline Code,Route,Net Amount,Reference\n"
    b"2024-12-25,2025-01-02,AA,AAA-BBB,100,first-reference\n"
    b"2025-01-06,2025-01-05,AA,AAA-BBB,(25),second-reference\n"
    b"2025-01-07,2025-02-08,BB,BBB-AAA,50,third-reference\n"
    b"2025-01-07,2025-02-08,BB,BBB-AAA,,duplicate-reference\n"
    b"2025-01-14,2025-01-21,CC,CCC-DDD,1000,open-week-reference\n"
)


class BrowserBookingAnalyticsTests(TestCase):
    def test_builds_aggregate_views_and_keeps_original_training_dates(self):
        prepared = prepare_browser_bookings(CSV, cutoff=pd.Timestamp("2025-01-14"))

        self.assertEqual(prepared.training_dates, ["2024-12-25", "2025-01-06", "2025-01-07", "2025-01-07"])
        self.assertEqual(prepared.data_through, "2025-01-07")
        self.assertEqual(
            [(row.record_date.date(), row.booking_value) for row in prepared.weekly_records],
            [(date(2024, 12, 24), 1), (date(2024, 12, 31), 1), (date(2025, 1, 7), 2)],
        )
        overall = BusinessAnalyticsResponse.model_validate(prepared.business_analytics["overall"])
        scoped = BusinessAnalyticsResponse.model_validate(prepared.business_analytics["2025"])
        self.assertEqual(overall.total_transaction_count, 4)
        self.assertEqual(overall.total_revenue, 125)
        self.assertEqual(scoped.total_revenue, 25)
        self.assertEqual(scoped.data_quality.duplicate_rows, 1)
        self.assertEqual(scoped.data_quality.invalid_travel_date, 1)
        self.assertEqual(scoped.avg_lead_time_days, 32.0)
        self.assertEqual(scoped.available_years, ["2024", "2025"])
        self.assertNotIn("-reference", str(prepared.business_analytics))
        self.assertNotIn("CCC-DDD", str(prepared.business_analytics))

    def test_browser_analytics_matches_persisted_native_snapshot_views(self):
        clock = self.enterContext(patch("core.config.datetime", wraps=datetime))
        clock.now.return_value = datetime(2025, 1, 14, 0, 1, tzinfo=MANILA_TIMEZONE)
        prepared = prepare_browser_bookings(CSV)
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine, tables=[TrainingDataLog.__table__, DatasetSnapshot.__table__])
        with Session(engine) as db:
            ingest_csv(CSV, db)
            snapshot = db.scalar(select(DatasetSnapshot))
            records = db.scalars(select(TrainingDataLog).order_by(TrainingDataLog.record_date)).all()

            for year, view in prepared.business_analytics.items():
                with self.subTest(year=year):
                    native = BusinessAnalyticsResponse.model_validate(business_analytics_view(snapshot, year, records))
                    browser = BusinessAnalyticsResponse.model_validate(view)
                    self.assertEqual(
                        native.model_dump(exclude={"generated_at"}),
                        browser.model_dump(exclude={"generated_at"}),
                    )

    def test_malformed_input_produces_no_browser_snapshot(self):
        for invalid in (
            b"Booking Date,Reference\nnot-a-date,private-reference\n",
            b"Booking Date,Reference\n2025-01-01,private-reference,extra\n",
        ):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "booking date|column headers"):
                prepare_browser_bookings(invalid)

    def test_browser_omits_free_text_airline_and_route_labels(self):
        prepared = prepare_browser_bookings(
            b"Booking Date,Airline Code,Route\n"
            b"2025-01-01,Customer-private,Customer-private\n"
            b"2025-01-01,AA,AAA-BBB\n"
            b"2025-01-02,AA,\n",
            cutoff=pd.Timestamp("2025-01-14"),
        )

        overall = prepared.business_analytics["overall"]
        self.assertEqual(overall["top_airlines"], [{"airline_code": "AA", "count": 2, "pct": 100.0}])
        self.assertEqual(overall["top_routes"], [{"route": "AAA-BBB", "count": 1, "pct": 100.0}])
        self.assertNotIn("Customer-private", str(overall))
        self.assertEqual(overall["data_quality"]["missing_route"], 0)
