"""Store the current hosted report in PostgreSQL."""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import and_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from domain.models import PublishedForecast


CURRENT_REPORT_ID = 1


class PublicationConflictError(Exception):
    """The current report is newer, or the same ID has different results."""


def fetch_current_report(db: Session) -> PublishedForecast | None:
    return db.get(PublishedForecast, CURRENT_REPORT_ID, populate_existing=True)


def store_current_report(
    db: Session, run_id: UUID, run_created_at: datetime, snapshot: dict,
) -> datetime:
    """Replace the current row atomically, or return the receipt for an exact retry."""
    candidate = insert(PublishedForecast).values(
        id=CURRENT_REPORT_ID,
        run_id=run_id,
        run_created_at=run_created_at,
        published_at=datetime.now(timezone.utc),
        snapshot=snapshot,
    )
    statement = candidate.on_conflict_do_update(
        index_elements=[PublishedForecast.id],
        set_={
            "run_id": candidate.excluded.run_id,
            "run_created_at": candidate.excluded.run_created_at,
            "published_at": candidate.excluded.published_at,
            "snapshot": candidate.excluded.snapshot,
        },
        where=and_(
            PublishedForecast.run_id != candidate.excluded.run_id,
            PublishedForecast.run_created_at < candidate.excluded.run_created_at,
        ),
    ).returning(PublishedForecast.published_at)
    published_at = db.execute(statement).scalar_one_or_none()
    if published_at is None:
        # PostgreSQL holds the conflicting row lock until this transaction ends.
        current = fetch_current_report(db)
        if current is None or current.run_id != run_id or current.snapshot != snapshot:
            raise PublicationConflictError()
        published_at = current.published_at
    db.commit()
    return published_at
