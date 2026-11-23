import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import TestCase, skipUnless
from unittest.mock import patch
from uuid import UUID, uuid4

from jose import jwt
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session


VIEWER_ID = UUID("a21aced1-57f6-430c-9787-26986335154b")
RUN_ID = "897a62f3-0d80-4bc2-a48e-3d98afbaefcf"
UNCONNECTED_DATABASE = "postgresql://synthetic@localhost/xoco_synthetic"


def synthetic_publication():
    return {
        "snapshot": {
            "schema_version": 1,
            "run_id": RUN_ID,
            "model_id": 17,
            "run_created_at": "2026-11-20T02:00:00Z",
            "data_through": "2026-11-19",
            "business_analytics": {"overall": {
                "generated_at": "2026-11-20T02:00:00Z",
                "date_coverage": {
                    "start_date": "2026-11-19T00:00:00Z",
                    "end_date": "2026-11-19T00:00:00Z",
                    "span_weeks": 1,
                },
                "total_transaction_count": 12,
                "total_weekly_records": 1,
                "total_revenue": 1200,
                "avg_weekly_bookings": 12,
                "peak_week_date": "2026-11-19T00:00:00Z",
                "peak_week_bookings": 12,
                "growth_rate": 0,
                "bookings_by_year": [{"year": "2026", "bookings": 12}],
                "bookings_by_month": [{"month": "2026-11", "bookings": 12}],
                "holiday_breakdown": {"holiday_weeks": 0, "non_holiday_weeks": 1, "holiday_pct": 0},
            }},
            "forecast_graph": {"data": [
                {"date": "2026-11-23T00:00:00Z", "predicted": 16},
            ]},
            "forecast_outlook": {
                "forecasted_bookings_2w": 32,
                "highest_forecast_week_date": "2026-11-23T00:00:00Z",
                "highest_forecast_week_value": 16,
                "forecasted_bookings_10w": 160,
                "critical_weeks": [],
            },
            "strategic_actions": {
                "actions": [],
                "generated_for_period": "Synthetic November",
            },
        },
        "published_at": "2026-11-20T02:30:00Z",
    }


def request(app, method, path, *, token=None, body=None):
    async def call():
        headers = []
        if token:
            headers.append((b"authorization", f"Bearer {token}".encode()))
        if body is not None:
            headers.append((b"content-type", b"application/json"))
        messages = []
        waiting = asyncio.Event()

        async def receive():
            if not waiting.is_set():
                waiting.set()
                return {"type": "http.request", "body": json.dumps(body).encode() if body else b""}
            await asyncio.Future()

        async def send(message):
            messages.append(message)

        await app({
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1",
            "scheme": "http",
            "method": method,
            "path": path,
            "root_path": "",
            "query_string": b"",
            "client": ("192.0.2.30", 1111),
            "server": ("localhost", 8000),
            "headers": headers,
        }, receive, send)
        start, *chunks = messages
        response_body = b"".join(item.get("body", b"") for item in chunks)
        return start["status"], dict(start["headers"]), json.loads(response_body)

    return asyncio.run(call())


class HostedViewerTests(TestCase):
    endpoint = "/api/viewer/published-forecast"

    def setUp(self):
        self.enterContext(patch.dict(os.environ, {
            "DATABASE_URL": UNCONNECTED_DATABASE,
            "JWT_SECRET_KEY": "only-used-for-synthetic-tests",
        }))
        from api.hosted import app
        from core.security import JWT_ALGORITHM, JWT_SECRET_KEY
        from domain.auth_models import User, UserRole

        self.app = app
        self.active_user = User(id=VIEWER_ID, is_active=True, role=UserRole.VIEWER)

        def signed_token(expires, typ="access"):
            return jwt.encode(
                {"sub": str(VIEWER_ID), "typ": typ, "exp": expires},
                JWT_SECRET_KEY,
                algorithm=JWT_ALGORITHM,
            )

        now = datetime.now(timezone.utc)
        self.access = signed_token(now + timedelta(minutes=5))
        self.expired = signed_token(now - timedelta(minutes=5))
        self.refresh = signed_token(now + timedelta(minutes=5), "refresh")
        self.lookup = self.enterContext(patch(
            "api.dependencies.auth.auth_repo.fetch_user_by_id", return_value=self.active_user,
        ))
        self.store = self.enterContext(patch("services.published_forecast_service.fetch_current_report"))

    def test_staff_access_is_rechecked_before_reading_the_report(self):
        data = synthetic_publication()
        data["snapshot"]["raw_csv"] = "synthetic field that must be stripped"
        self.store.return_value = SimpleNamespace(**data)

        status, headers, body = request(self.app, "GET", self.endpoint, token=self.access)

        self.assertEqual(status, 200, body)
        self.assertIn(b"no-store", headers[b"cache-control"])
        self.assertEqual(body["snapshot"]["run_id"], RUN_ID)
        self.assertEqual(body["snapshot"]["forecast_graph"]["data"][0]["predicted"], 16)
        self.assertNotIn("raw_csv", body["snapshot"])

        self.active_user.is_active = False
        status, _, _ = request(self.app, "GET", self.endpoint, token=self.access)
        self.assertEqual(status, 401)
        self.store.assert_called_once()

    def test_unauthorized_never_reads_publication_and_expiry_uses_refresh_signal(self):
        for token in (None, "synthetic-invalid-token", self.refresh, self.expired):
            with self.subTest(token=token):
                status, headers, body = request(self.app, "GET", self.endpoint, token=token)
                self.assertEqual(status, 401)
                self.assertIn(b"no-store", headers[b"cache-control"])
                expected = "token_expired" if token == self.expired else "request_failed"
                self.assertEqual(body["error"]["code"], expected)
        self.store.assert_not_called()
        self.lookup.assert_not_called()

    def test_empty_table_is_distinct_from_invalid_or_unavailable_data(self):
        cases = (
            (None, 404, "no_published_forecast"),
            (SimpleNamespace(snapshot=None, published_at=None), 503, "forecast_unavailable"),
        )
        for report, expected_status, expected_code in cases:
            with self.subTest(code=expected_code):
                self.store.return_value = report
                status, headers, body = request(self.app, "GET", self.endpoint, token=self.access)
                self.assertEqual(status, expected_status)
                self.assertEqual(body["error"]["code"], expected_code)
                self.assertIn(b"no-store", headers[b"cache-control"])

        self.store.side_effect = SQLAlchemyError("synthetic private database details")
        status, _, body = request(self.app, "GET", self.endpoint, token=self.access)
        self.assertEqual((status, body["error"]["code"]), (503, "forecast_unavailable"))
        self.assertNotIn("synthetic private", json.dumps(body))

    def test_only_session_and_read_routes_are_available(self):
        for method, path in (
            ("POST", "/api/upload"),
            ("POST", "/api/retrain"),
            ("POST", "/api/publications/17"),
            ("GET", "/admin/users"),
            ("POST", "/auth/register"),
            ("POST", "/auth/forgot-password"),
            ("POST", self.endpoint),
        ):
            with self.subTest(method=method, path=path):
                status, _, _ = request(self.app, method, path, token=self.access)
                expected = 405 if path == self.endpoint else 404
                self.assertEqual(status, expected)


@skipUnless(os.getenv("XOCOMPASS_TEST_POSTGRES_URL"), "Requires disposable synthetic PostgreSQL")
class HostedLoginPostgresTests(TestCase):
    def test_password_login_rotation_logout_and_account_revocation(self):
        self.enterContext(patch.dict(os.environ, {
            "DATABASE_URL": UNCONNECTED_DATABASE,
            "JWT_SECRET_KEY": "only-used-for-synthetic-tests",
        }))
        from api.dependencies.auth import get_db
        from api.hosted import app
        from core.security import JWT_ALGORITHM, JWT_SECRET_KEY, hash_password
        from domain.auth_models import AuditLog, Base, RefreshToken, User, UserRole

        url = os.environ["XOCOMPASS_TEST_POSTGRES_URL"]
        admin_engine = create_engine(url)
        self.addCleanup(admin_engine.dispose)
        schema = f"synthetic_viewer_{uuid4().hex}"
        with admin_engine.begin() as connection:
            connection.execute(text(f"CREATE SCHEMA {schema}"))

        def drop_schema():
            with admin_engine.begin() as connection:
                connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))

        self.addCleanup(drop_schema)
        engine = create_engine(url, connect_args={"options": f"-c search_path={schema}"})
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine, tables=[User.__table__, RefreshToken.__table__, AuditLog.__table__])

        def synthetic_db():
            with Session(engine) as session:
                yield session

        self.enterContext(patch.dict(app.dependency_overrides, {get_db: synthetic_db}))
        store = self.enterContext(patch("services.published_forecast_service.fetch_current_report"))
        store.return_value = SimpleNamespace(**synthetic_publication())
        credentials = {"email": "synthetic-staff@example.com", "password": "Synthetic-staff-password-23"}
        with Session(engine) as session:
            session.add(User(
                id=VIEWER_ID, email=credentials["email"], full_name="Synthetic Staff",
                hashed_password=hash_password(credentials["password"]), role=UserRole.VIEWER,
                is_active=True,
            ))
            session.commit()

        wrong = {**credentials, "password": "Synthetic-wrong-password-88"}
        status, _, body = request(app, "POST", "/auth/login", body=wrong)
        self.assertEqual((status, body["error"]["code"]), (401, "request_failed"))

        status, headers, login = request(app, "POST", "/auth/login", body=credentials)
        self.assertEqual(status, 200, login)
        self.assertIn(b"no-store", headers[b"cache-control"])
        self.assertEqual(login["user"]["id"], str(VIEWER_ID))
        self.assertEqual(login["user"]["role"], "VIEWER")
        access = login["access_token"]
        status, _, me = request(app, "GET", "/auth/me", token=access)
        self.assertEqual(status, 200, me)
        self.assertEqual(me["email"], credentials["email"])

        endpoint = HostedViewerTests.endpoint
        status, _, body = request(app, "GET", endpoint, token=access)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["snapshot"]["run_id"], RUN_ID)

        claims = jwt.get_unverified_claims(access)
        claims["exp"] = int((datetime.now(timezone.utc) - timedelta(minutes=1)).timestamp())
        expired = jwt.encode(claims, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
        status, _, body = request(app, "GET", endpoint, token=expired)
        self.assertEqual((status, body["error"]["code"]), (401, "token_expired"))

        status, _, rotated = request(app, "POST", "/auth/refresh", body={"refresh_token": login["refresh_token"]})
        self.assertEqual(status, 200, rotated)
        self.assertNotEqual(rotated["refresh_token"], login["refresh_token"])
        status, _, body = request(app, "GET", endpoint, token=rotated["access_token"])
        self.assertEqual(status, 200, body)

        refresh = {"refresh_token": rotated["refresh_token"]}
        status, _, body = request(app, "POST", "/auth/logout", body=refresh)
        self.assertEqual(status, 200, body)
        status, _, body = request(app, "POST", "/auth/refresh", body=refresh)
        self.assertEqual((status, body["error"]["code"]), (401, "request_failed"))

        with Session(engine) as session:
            session.get(User, VIEWER_ID).is_active = False
            session.commit()
        status, _, body = request(app, "GET", endpoint, token=rotated["access_token"])
        self.assertEqual((status, body["error"]["code"]), (401, "request_failed"))
        self.assertEqual(store.call_count, 2)

    def test_reports_survive_failed_publish_and_retry_uses_original_receipt(self):
        self.enterContext(patch.dict(os.environ, {
            "DATABASE_URL": UNCONNECTED_DATABASE,
            "JWT_SECRET_KEY": "only-used-for-synthetic-tests",
            "XOCOMPASS_PUBLISHED_FORECAST_URL": "",
            "XOCOMPASS_PUBLISH_READ_TOKEN": "",
            "XOCOMPASS_BROWSER_PUBLISH_TOKEN": "",
        }))
        from api.dependencies.auth import get_db
        from api.hosted import app
        from core.security import JWT_ALGORITHM, JWT_SECRET_KEY
        from domain.auth_models import User, UserRole

        url = os.environ["XOCOMPASS_TEST_POSTGRES_URL"]
        admin_engine = create_engine(url)
        self.addCleanup(admin_engine.dispose)
        schema = f"synthetic_viewer_{uuid4().hex}"
        with admin_engine.begin() as connection:
            connection.execute(text(f"CREATE SCHEMA {schema}"))

        def drop_schema():
            with admin_engine.begin() as connection:
                connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))

        self.addCleanup(drop_schema)
        engine = create_engine(url, connect_args={"options": f"-c search_path={schema}"})
        self.addCleanup(engine.dispose)
        with engine.begin() as connection:
            connection.execute(text("""
                CREATE TABLE published_forecast (
                    id smallint PRIMARY KEY CHECK (id = 1),
                    run_id uuid NOT NULL,
                    run_created_at timestamptz NOT NULL,
                    published_at timestamptz NOT NULL,
                    snapshot jsonb NOT NULL
                )
            """))

        def synthetic_db():
            with Session(engine) as session:
                yield session

        self.enterContext(patch.dict(app.dependency_overrides, {get_db: synthetic_db}))
        user = User(id=VIEWER_ID, is_active=True, role=UserRole.ANALYST)
        self.enterContext(patch("api.dependencies.auth.auth_repo.fetch_user_by_id", return_value=user))
        token = jwt.encode({
            "sub": str(VIEWER_ID), "typ": "access",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        }, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)

        def publish(snapshot):
            return request(app, "POST", "/api/viewer/publish", token=token, body=snapshot)

        def read():
            return request(app, "GET", HostedViewerTests.endpoint, token=token)

        first = synthetic_publication()["snapshot"]
        second = {**first, "run_id": str(uuid4()), "run_created_at": "2026-11-20T03:00:00Z"}
        status, _, first_receipt = publish(first)
        self.assertEqual(status, 200, first_receipt)

        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE published_forecast ADD CONSTRAINT synthetic_failure CHECK (snapshot->>'run_id' = :run_id)"),
                               {"run_id": first["run_id"]})
        status, _, error = publish(second)
        self.assertEqual((status, error["error"]["code"]), (503, "publish_failed"))

        user.role = UserRole.VIEWER
        status, _, body = read()
        self.assertEqual((status, body["snapshot"]["run_id"]), (200, first["run_id"]))
        self.assertEqual(publish(second)[0], 403)

        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE published_forecast DROP CONSTRAINT synthetic_failure"))
        user.role = UserRole.ANALYST
        status, _, receipt = publish(second)
        self.assertEqual(status, 200, receipt)
        self.assertEqual(publish(second)[2], receipt)
        self.assertEqual(publish(first)[0], 409)
        self.assertEqual(publish({**second, "model_id": 1})[0], 409)
        self.assertEqual(read()[2]["snapshot"]["run_id"], second["run_id"])
