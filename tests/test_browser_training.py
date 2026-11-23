import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

from scripts import prepare_browser_training as prepare
from services.pipeline import browser_training as training


class BrowserTrainingTests(unittest.TestCase):
    def test_invalid_booking_date_prevents_training(self):
        with tempfile.TemporaryDirectory() as directory:
            csv_path = Path(directory) / "bookings.csv"
            csv_path.write_text("Booking Date,Reference\n2025-01-01,a\ninvalid,b\n")
            with self.assertRaisesRegex(training.BrowserTrainingError, "invalid booking date"):
                training.train_browser_csv(csv_path)

    def test_failed_grid_search_does_not_accept_fallback_model(self):
        calls = []

        def auto_arima(**options):
            calls.append(options)

        def failed_search(_):
            pmdarima.auto_arima(n_jobs=-1, stepwise=False)
            return {"gs_order": None, "gs_seasonal": None, "final_order": (1, 0, 1)}

        pmdarima = SimpleNamespace(auto_arima=auto_arima)
        with self.assertRaisesRegex(training.BrowserTrainingError, "full model search"):
            training._search_model({}, failed_search, pmdarima)

        self.assertEqual(calls, [{"n_jobs": 1, "stepwise": False, "low_memory": True}])
        self.assertIs(pmdarima.auto_arima, auto_arima)

    def test_csv_validation_keeps_only_booking_dates(self):
        from services.booking_analytics import prepare_browser_bookings

        prepared = prepare_browser_bookings(b"Booking Date,Reference\n2025-01-01,private-reference\n")

        self.assertEqual(prepared.training_dates, ["2025-01-01"])
        self.assertNotIn("private-reference", str(prepared.business_analytics))


class BrowserBundleTests(unittest.TestCase):
    def test_failed_checksum_leaves_no_partial_release(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sandbox = root / "sandbox"
            runtime = sandbox / "runtime"
            runtime.mkdir(parents=True)
            (runtime / "pyodide-lock.json").write_text("{}")
            output = root / "training"

            with self.assertRaisesRegex(ValueError, "Checksum mismatch"):
                prepare.prepare_browser_training(sandbox, output)

            self.assertFalse(output.exists())

    def test_unrelated_sandbox_files_are_not_written_to_source_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_bytes = io.BytesIO()
            with ZipFile(archive_bytes, mode="w") as archive:
                archive.writestr("vendor/locket/__init__.py", "# sample vendor source")
                archive.writestr("benchmark.py", "raise Exception('sandbox only')")
                archive.writestr("private.csv", "Private Data")
            output = root / "source.zip"
            with ZipFile(archive_bytes) as archive:
                prepare._write_source_archive(archive, output)
            with ZipFile(output) as archive:
                paths = set(archive.namelist())

        self.assertIn("services/pipeline/browser_training.py", paths)
        self.assertIn("services/booking_analytics.py", paths)
        self.assertIn("services/forecast_publication_views.py", paths)
        self.assertIn("vendor/locket/__init__.py", paths)
        self.assertNotIn("benchmark.py", paths)
        self.assertNotIn("private.csv", paths)
