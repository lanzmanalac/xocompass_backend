"""Vercel entry point for staff sign-in and forecast publication."""

import json
import logging
import os
import re
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from jose import ExpiredSignatureError
from slowapi.errors import RateLimitExceeded
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import ClientDisconnect
from starlette.responses import JSONResponse, Response

from api.dependencies.auth import require_analyst, require_any
from api.routers.auth import session_router
from api.schemas import ErrorDetail, ErrorResponse, PublicationSnapshot, PublishedForecastResponse
from core.rate_limit import limiter
from services.publication_delivery import (
    PublicationDeliveryError,
    PublicationTarget,
    deliver_snapshot,
)
from services.published_forecast_service import (
    ForecastUnavailableError,
    NoPublishedForecastError,
    read_published_forecast,
)

database_url = os.getenv("DATABASE_URL")
if not database_url or make_url(database_url).get_backend_name() != "postgresql":
    raise RuntimeError("The hosted viewer requires a PostgreSQL DATABASE_URL.")

logger = logging.getLogger(__name__)
NO_STORE = "private, no-store"
MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024
PUBLISH_CLOCK_SKEW = timedelta(minutes=5)

app = FastAPI(title="XoCompass Staff Viewer", docs_url=None, redoc_url=None, openapi_url=None)
app.state.limiter = limiter
app.include_router(session_router, prefix="/auth")


def error_response(
    status: int, code: str, message: str, headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    error = ErrorResponse(error=ErrorDetail(code=code, message=message))
    return JSONResponse(
        error.model_dump(), status_code=status, headers=headers,
    )


@app.middleware("http")
async def prevent_caching(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    response = await call_next(request)
    response.headers["Cache-Control"] = NO_STORE
    return response


@app.exception_handler(HTTPException)
def handle_http_error(request: Request, exc: HTTPException) -> JSONResponse:
    code = "not_found" if exc.status_code == 404 else "request_failed"
    return error_response(exc.status_code, code, str(exc.detail), headers=exc.headers)


@app.exception_handler(RequestValidationError)
def handle_invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
    return error_response(400, "bad_request", "Request validation failed.")


@app.exception_handler(ExpiredSignatureError)
def handle_expired_token(request: Request, exc: ExpiredSignatureError) -> JSONResponse:
    return error_response(401, "token_expired", "Token has expired.", {"WWW-Authenticate": "Bearer"})


@app.exception_handler(RateLimitExceeded)
def handle_rate_limit(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return error_response(429, "rate_limited", "Too many requests. Please try again shortly.")


@app.exception_handler(SQLAlchemyError)
def handle_database_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    logger.error("Staff account database operation failed (%s).", type(exc).__name__)
    return error_response(503, "auth_unavailable", "Sign-in is temporarily unavailable.")


@app.exception_handler(NoPublishedForecastError)
def handle_empty_publication(request: Request, exc: NoPublishedForecastError) -> JSONResponse:
    return error_response(404, "no_published_forecast", "No forecast has been published yet.")


@app.exception_handler(ForecastUnavailableError)
def handle_unavailable_forecast(request: Request, exc: ForecastUnavailableError) -> JSONResponse:
    return error_response(503, "forecast_unavailable", "The published forecast is temporarily unavailable.")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "xocompass-staff-viewer"}


@app.get("/api/viewer/published-forecast", dependencies=[Depends(require_any)])
def get_published_forecast() -> PublishedForecastResponse:
    return read_published_forecast()


@app.post("/api/viewer/publish", dependencies=[Depends(require_analyst)])
async def publish_browser_forecast(request: Request) -> JSONResponse:
    """Forward a validated aggregate snapshot to the private receiver."""
    target = PublicationTarget.for_hosted_browser()
    if target is None:
        return error_response(503, "publish_unavailable", "Browser publishing is unavailable.")

    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        return error_response(415, "expected_json", "Send the dashboard snapshot as JSON.")

    invalid_snapshot = "Send a complete dashboard snapshot with a forward forecast."
    too_large = "These dashboard results are too large to publish."
    body = bytearray()
    try:
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_SNAPSHOT_BYTES:
                return error_response(413, "snapshot_too_large", too_large)
            body.extend(chunk)
        snapshot = PublicationSnapshot.model_validate_json(body, strict=True, extra="forbid")
        if (
            "overall" not in snapshot.business_analytics
            or any(key != "overall" and not re.fullmatch(r"[0-9]{4}", key) for key in snapshot.business_analytics)
            or snapshot.run_created_at.utcoffset() is None
            or not 0 < snapshot.model_id <= 2**53 - 1
            or not any(point.actual is None and point.predicted is not None for point in snapshot.forecast_graph.data)
        ):
            return error_response(400, "invalid_snapshot", invalid_snapshot)
        run_created_at = snapshot.run_created_at.astimezone(timezone.utc)
        # The receiver orders publications by this timestamp. A clock set well
        # ahead of UTC could otherwise lock out later runs.
        if run_created_at > datetime.now(timezone.utc) + PUBLISH_CLOCK_SKEW:
            return error_response(
                400, "invalid_snapshot", "The run timestamp is in the future. Check the office PC clock."
            )
        canonical = snapshot.model_dump(mode="json", exclude_unset=True)
        canonical["schema_version"] = snapshot.schema_version
        run_id = str(snapshot.run_id)
        canonical["run_created_at"] = run_created_at.isoformat().replace("+00:00", "Z")
        payload = json.dumps(
            canonical, separators=(",", ":"), sort_keys=True, allow_nan=False, ensure_ascii=False,
        ).encode("utf-8")
    except (ClientDisconnect, ValueError, OverflowError):
        return error_response(400, "invalid_snapshot", invalid_snapshot)
    if len(payload) > MAX_SNAPSHOT_BYTES:
        return error_response(413, "snapshot_too_large", too_large)

    try:
        published_at = await run_in_threadpool(deliver_snapshot, target, payload, run_id)
    except PublicationDeliveryError as exc:
        code = "publish_conflict" if exc.status_code == 409 else "publish_failed"
        return error_response(exc.status_code, code, str(exc))
    return JSONResponse({
        "run_id": run_id,
        "published_at": published_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
    })
