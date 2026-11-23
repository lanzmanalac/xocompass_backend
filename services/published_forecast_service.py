"""Read a validated dashboard snapshot from the private publication endpoint."""

import json
import os
from urllib.parse import urlsplit

import requests

from api.schemas import PublishedForecastResponse

MAX_PUBLICATION_BYTES = 3 * 1024 * 1024


class NoPublishedForecastError(Exception):
    """The publication store has no current run."""


class ForecastUnavailableError(Exception):
    """The publication store could not provide a usable response."""


def read_published_forecast() -> PublishedForecastResponse:
    url = os.getenv("XOCOMPASS_PUBLISHED_FORECAST_URL", "").strip()
    token = os.getenv("XOCOMPASS_PUBLISH_READ_TOKEN", "").strip()
    try:
        parsed_url = urlsplit(url)
        if (
            not token or parsed_url.scheme != "https" or not parsed_url.hostname
            or parsed_url.username is not None or parsed_url.password is not None
            or parsed_url.path != "/api/published-forecast"
            or parsed_url.query or parsed_url.fragment
        ):
            raise ForecastUnavailableError()
    except ValueError as exc:
        raise ForecastUnavailableError() from exc

    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    automation_token = os.getenv("XOCOMPASS_VERCEL_AUTOMATION_TOKEN", "").strip()
    if automation_token:
        headers["x-vercel-protection-bypass"] = automation_token

    try:
        with requests.get(
            url, headers=headers, timeout=(3, 5), allow_redirects=False, stream=True,
        ) as response:
            if response.status_code not in (200, 404):
                raise ForecastUnavailableError()

            payload = bytearray()
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if len(payload) + len(chunk) > MAX_PUBLICATION_BYTES:
                    raise ForecastUnavailableError()
                payload.extend(chunk)

            if response.status_code == 404:
                if json.loads(payload) == {"error": "no_published_forecast"}:
                    raise NoPublishedForecastError()
                raise ForecastUnavailableError()

            return PublishedForecastResponse.model_validate_json(payload)
    except (requests.RequestException, ValueError) as exc:
        raise ForecastUnavailableError() from exc
