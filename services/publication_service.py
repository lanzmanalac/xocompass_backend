"""Send a saved dashboard snapshot to the hosted publication endpoint."""

import json
from collections.abc import Callable

from repository.publication_outbox import PublicationOutbox, SavedPublication
from services.publication_delivery import (
    PublicationDeliveryError as PublicationDeliveryError,
    PublicationTarget as PublicationTarget,
    deliver_snapshot,
)


def publish_saved_snapshot(
    *,
    outbox: PublicationOutbox,
    key: str,
    target: PublicationTarget,
    prepare: Callable[[], str],
) -> SavedPublication:
    """Stage once before sending and retry the same bytes until Vercel confirms."""
    saved = outbox.get(key)
    if saved is None:
        outbox.stage(key, prepare())
        saved = outbox.get(key)
        if saved is None:
            raise RuntimeError("Could not read the saved publication.")
    if saved.published_at is not None:
        return saved

    run_id = json.loads(saved.payload)["run_id"]
    published_at = deliver_snapshot(target, saved.payload.encode("utf-8"), run_id)

    outbox.mark_published(key, published_at)
    return SavedPublication(saved.payload, published_at)
