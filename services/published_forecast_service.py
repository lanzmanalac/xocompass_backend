"""Read a validated dashboard snapshot from PostgreSQL."""

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from api.schemas import PublishedForecastResponse
from repository.published_forecast_repository import fetch_current_report


class NoPublishedForecastError(Exception):
    """The report table has no current run."""


class ForecastUnavailableError(Exception):
    """The report table could not provide a usable response."""


def read_published_forecast(db: Session) -> PublishedForecastResponse:
    try:
        report = fetch_current_report(db)
        if report is None:
            raise NoPublishedForecastError()
        return PublishedForecastResponse.model_validate({
            "snapshot": report.snapshot,
            "published_at": report.published_at,
        })
    except (SQLAlchemyError, ValidationError):
        raise ForecastUnavailableError() from None
