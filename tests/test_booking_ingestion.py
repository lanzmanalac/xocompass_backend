import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from zoneinfo import ZoneInfo

os.environ.setdefault("MPLBACKEND", "Agg")

import matplotlib.pyplot as plt
import pandas as pd
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from domain.models import Base, DatasetSnapshot, TrainingDataLog
from services.ingestion_service import ingest_csv
from services.pipeline.step1_ingestion import run_step1_data_ingestion


class TrainingInputTests(TestCase):
    def test_training_includes_recent_completed_week_and_excludes_open_week(self):
        manila_now = datetime(2026, 11, 17, 0, 1, tzinfo=ZoneInfo("Asia/Manila"))
        with TemporaryDirectory() as directory:
            csv = Path(directory) / "bookings.csv"
            csv.write_text(
                "Generation Date\n"
                "2025-12-08\n2025-12-22\n2025-12-22\n"
                "2026-11-16 23:55:00\n2026-11-16 12:00:00\n"
                "2026-11-17 00:00:00\n",
                encoding="utf-8",
            )
            self.addCleanup(plt.close, "all")
            with patch("core.config.datetime", wraps=datetime) as clock:
                clock.now.return_value = manila_now
                weekly = run_step1_data_ingestion(csv)["weekly_df"]

        self.assertEqual(weekly.index[-1], pd.Timestamp("2026-11-16"))
        self.assertEqual(weekly.iloc[-1]["bookings_weekly"], 2)
        self.assertEqual(weekly["bookings_weekly"].sum(), 5)

    def test_retraining_export_keeps_utc_week_start_in_its_manila_week(self):
        with patch.dict(os.environ, {"DATABASE_URL": "sqlite://"}):
            from services.pipeline import orchestrator

        latest_week = datetime(2026, 11, 9, 16, tzinfo=timezone.utc)
        records = [
            TrainingDataLog(record_date=latest_week - timedelta(weeks=offset), booking_value=1)
            for offset in reversed(range(20))
        ]
        with TemporaryDirectory() as directory, patch.object(orchestrator, "SessionLocal") as sessions:
            db = sessions.return_value.__enter__.return_value
            db.query.return_value.order_by.return_value.all.return_value = records
            csv_path, _ = orchestrator.export_db_to_csv(Path(directory) / "training.csv")
            exported_dates = pd.read_csv(csv_path)["Generation Date"]

        self.assertEqual(exported_dates.iloc[-1], "2026-11-10")


class CsvReplacementTests(TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(
            self.engine, tables=[TrainingDataLog.__table__, DatasetSnapshot.__table__]
        )
        clock = self.enterContext(patch("core.config.datetime", wraps=datetime))
        clock.now.return_value = datetime(
            2026, 11, 17, 0, 1, tzinfo=ZoneInfo("Asia/Manila")
        )
        with Session(self.engine) as db:
            ingest_csv(
                b"Generation Date,Travel Date,Net Amount\n"
                b"2026-01-12,2026-01-13,120\n2026-01-19,2026-01-20,250\n",
                db,
            )
        self.original_data = self.read_data()

    def read_data(self):
        with Session(self.engine) as db:
            weeks = db.scalars(select(TrainingDataLog).order_by(TrainingDataLog.record_date))
            snapshots = db.scalars(select(DatasetSnapshot).order_by(DatasetSnapshot.id))
            return (
                [(row.record_date.date(), row.booking_value, row.weekly_revenue) for row in weeks],
                [(row.ingestion_batch_id, row.total_transaction_count) for row in snapshots],
            )

    def test_malformed_csv_preserves_previous_data(self):
        with Session(self.engine) as db:
            with self.assertRaises(pd.errors.ParserError):
                ingest_csv(b'Generation Date,Net Amount\n"2026-01-19,400\n', db)
            db.rollback()

        self.assertEqual(self.read_data(), self.original_data)

    def test_csv_with_an_invalid_booking_date_preserves_previous_data(self):
        with Session(self.engine) as db:
            with self.assertRaisesRegex(ValueError, "invalid booking date"):
                ingest_csv(b"Generation Date\n2026-01-19\nnot-a-date\n", db)

        self.assertEqual(self.read_data(), self.original_data)

    def test_csv_with_wrong_row_width_preserves_previous_data(self):
        candidates = (
            b"Generation Date,Net Amount\n2026-01-19,100,extra\n",
            b"Generation Date,Net Amount\n2026-01-19,100\n2026-01-20\n",
        )
        for csv in candidates:
            with self.subTest(csv=csv), Session(self.engine) as db:
                with self.assertRaisesRegex(ValueError, "column headers"):
                    ingest_csv(csv, db)

                self.assertEqual(self.read_data(), self.original_data)

    def test_csv_without_a_booking_date_header_preserves_previous_data(self):
        for header in (b"Travel Date", b"Updated Date", b"Booking Updated Date"):
            with self.subTest(header=header), Session(self.engine) as db:
                with self.assertRaisesRegex(ValueError, "booking date column"):
                    ingest_csv(header + b"\n2026-11-16\n", db)

                self.assertEqual(self.read_data(), self.original_data)

    def test_csv_without_any_completed_weeks_preserves_previous_data(self):
        candidates = (
            b"Generation Date\n",
            b"Generation Date\n2026-11-17\n2026-11-18\n",
        )
        for csv in candidates:
            with self.subTest(csv=csv), Session(self.engine) as db:
                with self.assertRaisesRegex(ValueError, "completed reporting weeks"):
                    ingest_csv(csv, db)

                self.assertEqual(self.read_data(), self.original_data)

    def test_snapshot_failure_rolls_back_replacement(self):
        with Session(self.engine) as db:
            with patch(
                "services.ingestion_service.compute_and_persist_dataset_snapshot",
                side_effect=RuntimeError("snapshot write failed"),
            ):
                with self.assertRaisesRegex(RuntimeError, "snapshot write failed"):
                    ingest_csv(
                        b"Generation Date,Travel Date,Net Amount\n"
                        b"2026-11-16,2026-11-17,210\n",
                        db,
                    )
            weeks = db.scalars(select(TrainingDataLog).order_by(TrainingDataLog.record_date))
            self.assertEqual(
                [(row.record_date.date(), row.booking_value, row.weekly_revenue) for row in weeks],
                self.original_data[0],
            )

        self.assertEqual(self.read_data(), self.original_data)

    def test_full_export_replaces_corrected_weeks_and_skips_open_week(self):
        with Session(self.engine) as db:
            result = ingest_csv(
                b"Travel Date,Booking Date\n"
                b"2026-01-30,2026-01-12\n"
                b"2026-01-30,2026-01-19\n"
                b"2026-01-30,2026-01-19\n"
                b"2026-11-30,2026-11-16 23:55:00\n"
                b"2026-11-30,2026-11-17 00:00:00\n",
                db,
            )

        self.assertEqual(result["new_records"], 3)
        weeks, snapshots = self.read_data()
        self.assertEqual(
            weeks,
            [
                (date(2026, 1, 6), 1.0, None),
                (date(2026, 1, 13), 2.0, None),
                (date(2026, 11, 10), 1.0, None),
            ],
        )
        self.assertEqual(snapshots, [*self.original_data[1], (result["ingestion_batch_id"], 4)])

    def test_upload_compares_timezone_aware_dates_to_the_manila_week(self):
        with Session(self.engine) as db:
            ingest_csv(
                b"Generation Date,Travel Date\n"
                b"2026-11-16T15:55:00Z,2026-11-20T00:00:00Z\n"
                b"2026-11-16T16:00:00Z,2026-11-20T00:00:00Z\n",
                db,
            )

        weeks, _ = self.read_data()
        self.assertEqual(weeks, [(date(2026, 11, 10), 1.0, None)])
