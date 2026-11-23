import json
import os
from datetime import datetime, timedelta, timezone
from unittest import TestCase
from unittest.mock import patch

from jose import jwt
from requests import Timeout

from tests.test_hosted_viewer import (
    PUBLICATION_URL,
    READ_TOKEN,
    RUN_ID,
    UNCONNECTED_DATABASE,
    VIEWER_ID,
    request,
    synthetic_publication,
    upstream_response,
)


WRITE_TOKEN = "synthetic-browser-publisher"
AUTOMATION_TOKEN = "synthetic-deployment-protection"


class HostedPublishTests(TestCase):
    endpoint = "/api/viewer/publish"

    def setUp(self):
        self.enterContext(patch.dict(os.environ, {
            "DATABASE_URL": UNCONNECTED_DATABASE,
            "JWT_SECRET_KEY": "only-used-for-synthetic-tests",
            "XOCOMPASS_PUBLISHED_FORECAST_URL": PUBLICATION_URL,
            "XOCOMPASS_PUBLISH_READ_TOKEN": READ_TOKEN,
            "XOCOMPASS_BROWSER_PUBLISH_TOKEN": WRITE_TOKEN,
            "XOCOMPASS_VERCEL_AUTOMATION_TOKEN": AUTOMATION_TOKEN,
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
        self.receiver = self.enterContext(patch("requests.post"))
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

    def test_analyst_sends_same_snapshot_on_retry_using_server_credentials(self):
        self.receiver.side_effect = [
            Timeout("synthetic receiver did not respond"),
            upstream_response(200, self.receipt),
        ]

        status, _, body = self.publish()
        self.assertEqual(status, 502, body)

        status, headers, receipt = self.publish()
        self.assertEqual(status, 200, receipt)
        self.assertIn(b"no-store", headers[b"cache-control"])
        self.assertEqual(receipt, self.receipt)
        self.assertEqual(self.receiver.call_args_list[0], self.receiver.call_args_list[1])
        self.assertEqual(self.receiver.call_args.args, (PUBLICATION_URL,))
        call = self.receiver.call_args.kwargs
        self.assertEqual(call["headers"]["Authorization"], f"Bearer {WRITE_TOKEN}")
        self.assertEqual(call["headers"]["x-vercel-protection-bypass"], AUTOMATION_TOKEN)
        self.assertFalse(call["allow_redirects"])
        self.assertEqual(json.loads(call["data"]), self.snapshot)

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
        self.receiver.assert_not_called()

    def test_invalid_snapshot_does_not_reach_receiver(self):
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
        self.receiver.assert_not_called()

    def test_streamed_request_body_over_limit_is_rejected(self):
        status, _, body = self.publish(snapshot={"synthetic": "x" * (2 * 1024 * 1024)})

        self.assertEqual(status, 413, body)
        self.assertEqual(body["error"]["code"], "snapshot_too_large")
        self.receiver.assert_not_called()

    def test_missing_or_reused_server_credentials_disable_publish(self):
        for config in (
            {"XOCOMPASS_BROWSER_PUBLISH_TOKEN": ""},
            {"XOCOMPASS_BROWSER_PUBLISH_TOKEN": READ_TOKEN},
            {"XOCOMPASS_PUBLISH_READ_TOKEN": ""},
        ):
            with self.subTest(config=config), patch.dict(os.environ, config):
                status, _, body = self.publish()
                self.assertEqual(status, 503, body)
                self.assertEqual(body["error"]["code"], "publish_unavailable")
        self.receiver.assert_not_called()

    def test_admin_can_publish_but_receiver_url_and_media_type_are_checked(self):
        from domain.auth_models import UserRole

        self.user.role = UserRole.ADMIN
        with patch.dict(os.environ, {"XOCOMPASS_PUBLISHED_FORECAST_URL": "https://example.test/unrelated"}):
            status, _, body = self.publish()
            self.assertEqual(status, 503, body)
        status, _, body = request(self.app, "POST", self.endpoint, token=self.token)
        self.assertEqual(status, 415, body)
        self.receiver.assert_not_called()

        self.receiver.return_value = upstream_response(200, self.receipt)
        status, _, body = self.publish()
        self.assertEqual(status, 200, body)

    def test_receiver_conflict_or_invalid_receipt_are_not_success(self):
        cases = (
            (upstream_response(409, {"error": "stale_snapshot"}), 409),
            (upstream_response(302, {}), 502),
            (upstream_response(200, {**self.receipt, "run_id": "wrong-run"}), 502),
            (upstream_response(200, {**self.receipt, "published_at": "2026-11-20T02:30:00"}), 502),
        )
        for response, expected in cases:
            with self.subTest(status=response.status_code, expected=expected):
                self.receiver.return_value = response
                status, _, body = self.publish()
                self.assertEqual(status, expected, body)
