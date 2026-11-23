import json
import os
from datetime import datetime, timedelta, timezone
from unittest import TestCase
from unittest.mock import patch

from jose import jwt
from sqlalchemy.exc import SQLAlchemyError

from tests.test_hosted_viewer import (
    RUN_ID,
    UNCONNECTED_DATABASE,
    VIEWER_ID,
    request,
    synthetic_publication,
)


class HostedPublishTests(TestCase):
    endpoint = "/api/viewer/publish"

    def setUp(self):
        self.enterContext(patch.dict(os.environ, {
            "DATABASE_URL": UNCONNECTED_DATABASE,
            "JWT_SECRET_KEY": "only-used-for-synthetic-tests",
        }))
        from api.hosted import app
        from core.security import JWT_ALGORITHM, JWT_SECRET_KEY
        from domain.auth_models import User, UserRole

        self.app = app
        self.user = User(id=VIEWER_ID, is_active=True, role=UserRole.ANALYST)
        self.token = jwt.encode(
            {
                "sub": str(VIEWER_ID),
                "typ": "access",
                "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
            },
            JWT_SECRET_KEY,
            algorithm=JWT_ALGORITHM,
        )
        self.enterContext(patch(
            "api.dependencies.auth.auth_repo.fetch_user_by_id", return_value=self.user,
        ))
        self.store = self.enterContext(patch("api.hosted.store_current_report"))
        self.published_at = datetime(2026, 11, 20, 2, 30, tzinfo=timezone.utc)
        self.receipt = {
            "run_id": RUN_ID,
            "published_at": "2026-11-20T02:30:00Z",
        }
        self.snapshot = synthetic_publication()["snapshot"]

    def publish(self, *, snapshot=None):
        return request(
            self.app, "POST", self.endpoint, token=self.token,
            body=self.snapshot if snapshot is None else snapshot,
        )

    def test_analyst_passes_the_validated_snapshot_to_the_store(self):
        self.store.return_value = self.published_at

        status, headers, receipt = self.publish()
        self.assertEqual(status, 200, receipt)
        self.assertIn(b"no-store", headers[b"cache-control"])
        self.assertEqual(receipt, self.receipt)
        _, run_id, run_created_at, snapshot = self.store.call_args.args
        self.assertEqual(str(run_id), RUN_ID)
        self.assertEqual(run_created_at.utcoffset(), timedelta(0))
        self.assertEqual(snapshot, self.snapshot)

    def test_viewer_inactive_and_signed_out_users_cannot_publish(self):
        from domain.auth_models import UserRole

        status, _, _ = request(self.app, "POST", self.endpoint, body=self.snapshot)
        self.assertEqual(status, 401)

        self.user.role = UserRole.VIEWER
        status, _, _ = self.publish()
        self.assertEqual(status, 403)

        self.user.role = UserRole.ANALYST
        self.user.is_active = False
        status, _, _ = self.publish()
        self.assertEqual(status, 401)
        self.store.assert_not_called()

    def test_invalid_snapshot_does_not_reach_store(self):
        invalid = synthetic_publication()["snapshot"]
        invalid["forecast_graph"]["data"][0]["raw_csv"] = "synthetic private row"
        invalid_analytics_key = synthetic_publication()["snapshot"]
        invalid_analytics_key["business_analytics"]["synthetic private key"] = (
            invalid_analytics_key["business_analytics"]["overall"]
        )
        invalid_timestamp = synthetic_publication()["snapshot"]
        invalid_timestamp["run_created_at"] = "2026-11-20T02:00:00"
        future_timestamp = synthetic_publication()["snapshot"]
        future_timestamp["run_created_at"] = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        invalid_numeric = synthetic_publication()["snapshot"]
        invalid_numeric["business_analytics"]["overall"]["total_revenue"] = float("inf")
        no_forward_forecast = synthetic_publication()["snapshot"]
        no_forward_forecast["forecast_graph"]["data"][0]["actual"] = 12

        for value in (
            invalid, invalid_analytics_key, invalid_timestamp, future_timestamp,
            invalid_numeric, no_forward_forecast,
        ):
            with self.subTest(value=value):
                status, _, body = self.publish(snapshot=value)
                self.assertEqual(status, 400, body)
                self.assertEqual(body["error"]["code"], "invalid_snapshot")
                self.assertNotIn("synthetic private", json.dumps(body))
        self.store.assert_not_called()

    def test_streamed_request_body_over_limit_is_rejected(self):
        status, _, body = self.publish(snapshot={"synthetic": "x" * (2 * 1024 * 1024)})

        self.assertEqual(status, 413, body)
        self.assertEqual(body["error"]["code"], "snapshot_too_large")
        self.store.assert_not_called()

    def test_admin_can_publish_json(self):
        from domain.auth_models import UserRole

        self.user.role = UserRole.ADMIN
        status, _, body = request(self.app, "POST", self.endpoint, token=self.token)
        self.assertEqual(status, 415, body)
        self.store.assert_not_called()

        self.store.return_value = self.published_at
        status, _, body = self.publish()
        self.assertEqual(status, 200, body)

    def test_database_error_does_not_expose_private_data(self):
        self.store.side_effect = SQLAlchemyError("synthetic private database details")
        status, _, body = self.publish()
        self.assertEqual((status, body["error"]["code"]), (503, "publish_failed"))
        self.assertNotIn("synthetic private", json.dumps(body))
