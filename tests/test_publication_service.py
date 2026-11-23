import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

from requests import Response, Timeout

from services.publication_service import (
    PublicationDeliveryError,
    PublicationOutbox,
    PublicationTarget,
    publish_saved_snapshot,
)


class PublicationTests(TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        self.outbox_path = Path(directory) / "publications.db"
        self.target = PublicationTarget(
            url="https://forecast.example.test/api/published-forecast",
            token="synthetic-publisher-token",
            automation_token="synthetic-protection-token",
        )
        self.key = "23:2026-11-17T01:05:00+00:00"
        self.snapshot = json.dumps(
            {
                "run_id": "41b4c8f3-35c3-49c7-8394-d59aa69ca204",
                "data_through": "2026-11-16",
            }
        )
        self.prepare = Mock(return_value=self.snapshot)

    @staticmethod
    def response(status, payload):
        response = Response()
        response.status_code = status
        response._content = json.dumps(payload).encode()
        return response

    def publish(self):
        return publish_saved_snapshot(
            outbox=PublicationOutbox(self.outbox_path),
            key=self.key,
            target=self.target,
            prepare=self.prepare,
        )

    @patch("services.publication_delivery.requests.post")
    def test_failed_publish_retries_the_same_saved_snapshot_after_restart(self, post):
        post.side_effect = [
            Timeout(),
            self.response(
                200,
                {
                    "run_id": "41b4c8f3-35c3-49c7-8394-d59aa69ca204",
                    "published_at": "2026-11-17T01:12:00.000Z",
                },
            ),
        ]

        with self.assertRaises(PublicationDeliveryError):
            self.publish()

        staged = PublicationOutbox(self.outbox_path).get(self.key)
        self.assertEqual(staged.payload, self.snapshot)
        self.assertIsNone(staged.published_at)
        self.prepare.side_effect = AssertionError("retry rebuilt the saved run")

        published = self.publish()
        replay = self.publish()

        self.assertEqual(published, replay)
        self.assertEqual(published.published_at.isoformat(), "2026-11-17T01:12:00+00:00")
        self.assertEqual(post.call_count, 2)
        self.assertEqual(post.call_args_list[0], post.call_args_list[1])
        self.assertEqual(post.call_args.kwargs["data"], self.snapshot.encode())
        self.assertFalse(post.call_args.kwargs["allow_redirects"])

    @patch("services.publication_delivery.requests.post")
    def test_missing_or_wrong_receipt_never_marks_snapshot_published(self, post):
        post.return_value = self.response(
            200,
            {"run_id": "unexpected", "published_at": "2026-11-17T01:12:00.000Z"},
        )
        with self.assertRaises(PublicationDeliveryError):
            self.publish()

        self.assertIsNone(PublicationOutbox(self.outbox_path).get(self.key).published_at)

    @patch("services.publication_delivery.requests.post")
    def test_cloud_conflict_preserves_draft_and_reports_the_conflict(self, post):
        post.return_value = self.response(409, {"error": "A newer run is already published."})
        with self.assertRaises(PublicationDeliveryError) as error:
            self.publish()

        self.assertEqual(error.exception.status_code, 409)
        self.assertIsNone(PublicationOutbox(self.outbox_path).get(self.key).published_at)
