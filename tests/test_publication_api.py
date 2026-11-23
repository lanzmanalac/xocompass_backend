import json
import os
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from requests import Response, Timeout
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from domain.models import Base, DatasetSnapshot, ForecastCache, ModelDiagnostic, SarimaxModel, TrainingDataLog
from services.ingestion_service import ingest_csv


class OfficePublicationTests(TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, {
            "DATABASE_URL": "sqlite://",
            "JWT_SECRET_KEY": "synthetic-unit-test-only-token",
            "XOCOMPASS_PUBLISH_URL": "https://forecast.example.test/api/published-forecast",
            "XOCOMPASS_PUBLISH_TOKEN": "synthetic-publisher-token",
            "XOCOMPASS_PUBLICATION_DB": str(Path(directory) / "publications.db"),
        }))
        clock = self.enterContext(patch("core.config.datetime", wraps=datetime))
        clock.now.return_value = datetime(2026, 11, 21, tzinfo=ZoneInfo("Asia/Manila"))
        self.engine = create_engine("sqlite://")
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(
            self.engine,
            tables=[model.__table__ for model in (
                SarimaxModel, DatasetSnapshot, TrainingDataLog, ForecastCache, ModelDiagnostic,
            )],
        )
        self.db = self.enterContext(Session(self.engine))
        self.seed_model()

    def seed_model(self):
        result = ingest_csv(b"Generation Date,Net Amount\n2026-11-10,10\n2026-11-16,20\n", self.db)
        model = SarimaxModel(
            model_name="Synthetic forecast",
            model_path="/office/private/never-export.joblib",
            created_at=datetime(2026, 11, 17, 9),
            ingestion_batch_id=result["ingestion_batch_id"],
        )
        self.db.add(model)
        self.db.flush()
        self.model_id = model.id
        self.db.add(ForecastCache(
            model_id=self.model_id,
            forecast_date=datetime(2026, 11, 23),
            predicted=14,
            lower_bound=10,
            upper_bound=18,
            periods_ahead=1,
            confidence_tier="HIGH",
            risk_flag="LOW",
        ))
        self.db.commit()

    @patch("services.publication_delivery.requests.post")
    def test_retry_uses_saved_results_after_local_booking_history_is_replaced(self, post):
        from api.main import get_publication_status, publish_forecast

        post.side_effect = Timeout()
        ready = get_publication_status(self.model_id, self.db, None)
        self.assertEqual((ready.state, ready.data_through.isoformat()), ("ready", "2026-11-16"))

        with self.assertRaises(HTTPException) as failure:
            publish_forecast(self.model_id, self.db, None)
        self.assertEqual(failure.exception.status_code, 502)
        self.assertEqual(get_publication_status(self.model_id, self.db, None).state, "pending")
        first_payload = post.call_args.kwargs["data"]
        body = json.loads(first_payload)
        self.assertEqual(body["business_analytics"]["overall"]["total_transaction_count"], 2)
        self.assertEqual(body["forecast_graph"]["data"][-1]["predicted"], 14)
        self.assertNotIn(b"never-export.joblib", first_payload)

        ingest_csv(b"Generation Date,Net Amount\n2026-11-09,99\n", self.db)
        post.side_effect = None
        receipt = Response()
        receipt.status_code = 200
        receipt._content = json.dumps({
            "run_id": body["run_id"],
            "published_at": "2026-11-21T11:00:00.000Z",
        }).encode()
        post.return_value = receipt

        finished = publish_forecast(self.model_id, self.db, None)

        self.assertEqual(finished.state, "published")
        self.assertEqual(post.call_args.kwargs["data"], first_payload)
        self.assertEqual(get_publication_status(self.model_id, self.db, None).state, "published")

    @patch("services.publication_delivery.requests.post")
    def test_unstaged_old_run_cannot_publish_with_a_different_ledger(self, post):
        from api.main import publish_forecast

        ingest_csv(b"Generation Date,Net Amount\n2026-11-05,99\n", self.db)
        with self.assertRaises(HTTPException) as failure:
            publish_forecast(self.model_id, self.db, None)

        self.assertEqual(failure.exception.status_code, 409)
        post.assert_not_called()

    @patch("services.publication_delivery.requests.post")
    def test_a_receipt_from_one_project_is_not_reused_for_another_project(self, post):
        from api.main import get_publication_status, publish_forecast

        def acknowledge(url, *, data, **kwargs):
            response = Response()
            response.status_code = 200
            response._content = json.dumps({
                "run_id": json.loads(data)["run_id"],
                "published_at": "2026-11-21T11:00:00Z",
            }).encode()
            return response

        post.side_effect = acknowledge
        publish_forecast(self.model_id, self.db, None)
        with patch.dict(os.environ, {
            "XOCOMPASS_PUBLISH_URL": "https://other.example.test/api/published-forecast",
        }):
            self.assertEqual(get_publication_status(self.model_id, self.db, None).state, "ready")
            publish_forecast(self.model_id, self.db, None)

        self.assertEqual(post.call_count, 2)
