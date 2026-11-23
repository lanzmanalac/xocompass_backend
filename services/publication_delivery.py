"""Deliver an approved snapshot to the hosted private publication receiver."""

import os
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlsplit

import requests


@dataclass(frozen=True)
class PublicationTarget:
    url: str
    token: str = field(repr=False)
    automation_token: str = field(default="", repr=False)

    @classmethod
    def from_environment(cls) -> "PublicationTarget | None":
        return cls._configured(
            os.getenv("XOCOMPASS_PUBLISH_URL", "").strip(),
            os.getenv("XOCOMPASS_PUBLISH_TOKEN", "").strip(),
        )

    @classmethod
    def for_hosted_browser(cls) -> "PublicationTarget | None":
        token = os.getenv("XOCOMPASS_BROWSER_PUBLISH_TOKEN", "").strip()
        read_token = os.getenv("XOCOMPASS_PUBLISH_READ_TOKEN", "").strip()
        if not read_token or token == read_token:
            return None
        return cls._configured(
            os.getenv("XOCOMPASS_PUBLISHED_FORECAST_URL", "").strip(),
            token,
            path="/api/published-forecast",
        )

    @classmethod
    def _configured(cls, url: str, token: str, *, path: str | None = None) -> "PublicationTarget | None":
        try:
            parsed = urlsplit(url)
        except ValueError:
            return None
        if (
            not token or parsed.scheme != "https" or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment
            or (path is not None and parsed.path != path)
        ):
            return None
        canonical_url = parsed._replace(
            netloc=parsed.netloc.lower(), path=parsed.path.rstrip("/")
        ).geturl()
        return cls(
            canonical_url, token, os.getenv("XOCOMPASS_VERCEL_AUTOMATION_TOKEN", "").strip()
        )

    def model_key(self, model_id: int, created_at: datetime) -> str:
        return f"{self.url}\n{model_id}:{created_at.isoformat()}"


class PublicationDeliveryError(Exception):
    def __init__(self, message: str, *, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def deliver_snapshot(target: PublicationTarget, payload: bytes, run_id: str) -> datetime:
    headers = {
        "Authorization": f"Bearer {target.token}",
        "Content-Type": "application/json",
    }
    if target.automation_token:
        headers["x-vercel-protection-bypass"] = target.automation_token

    retry_message = "Vercel did not confirm the publish. The saved results are ready to retry."
    try:
        response = requests.post(
            target.url, data=payload, headers=headers,
            timeout=(5, 20), allow_redirects=False,
        )
        if response.status_code == 409:
            raise PublicationDeliveryError(
                "A newer or conflicting forecast is already online. Select a newer saved run.",
                status_code=409,
            )
        if response.status_code != 200:
            raise PublicationDeliveryError(retry_message)
        receipt = response.json()
        published_at = datetime.fromisoformat(receipt["published_at"].replace("Z", "+00:00"))
        if receipt["run_id"] != run_id or published_at.utcoffset() is None:
            raise PublicationDeliveryError(retry_message)
    except (requests.RequestException, TypeError, AttributeError, KeyError, ValueError) as exc:
        raise PublicationDeliveryError(retry_message) from exc
    return published_at
