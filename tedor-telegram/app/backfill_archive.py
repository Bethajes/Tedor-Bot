"""Archive tutors that exist in SQLite but never reached the channel.

The Mini App used to accept uploads and submissions without ever calling the
storage service, so every tutor who applied through it sat at ``PENDING`` with
no archive message. Their profiles are intact — only the channel copy is
missing, so this re-archives them from what is already stored.

    python -m app.backfill_archive --dry-run
    python -m app.backfill_archive

``--dry-run`` reports exactly what would be sent and posts nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from app.database import init_db, session_scope
from app.enums import StorageStatus
from app.logging_config import configure_logging
from app.models import Tutor
from app.services.storage_service import (
    DocumentRef,
    TelegramStorageService,
    tutor_document_refs,
)

logger = logging.getLogger(__name__)

#: Telegram file IDs are long, URL-safe base64 strings. The Mini App used to
#: write a one-character placeholder when it had nothing to store, and posting
#: one of those makes Telegram reject the whole archive call — which would mark
#: an otherwise healthy tutor as failed.
_MIN_FILE_ID_LENGTH = 20


@dataclass
class BackfillReport:
    archived: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    #: Tutors whose stored references are unusable placeholders.
    damaged: dict[str, list[str]] = field(default_factory=dict)
    dry_run: bool = False


def usable_refs(refs: list[DocumentRef]) -> tuple[list[DocumentRef], list[str]]:
    """Split refs into postable ones and placeholder file IDs to report."""
    postable: list[DocumentRef] = []
    damaged: list[str] = []
    for ref in refs:
        if ref.is_archived:
            postable.append(ref)
            continue
        if len(ref.telegram_file_id or "") < _MIN_FILE_ID_LENGTH:
            damaged.append(f"{ref.document_type} (file id {ref.telegram_file_id!r})")
            continue
        postable.append(ref)
    return postable, damaged


def find_pending(session: Any) -> list[Tutor]:
    """Tutors whose record has never been archived."""
    return list(
        session.query(Tutor)
        .filter(Tutor.storage_status != str(StorageStatus.STORED))
        .order_by(Tutor.id)
        .all()
    )


async def run_backfill(dry_run: bool = False) -> BackfillReport:
    """Archive every tutor that is not yet in the channel."""
    from app.services.telegram_client import get_bot

    init_db()
    report = BackfillReport(dry_run=dry_run)

    with session_scope() as session:
        pending = find_pending(session)
        snapshot = [
            (tutor.public_tutor_id, tutor_document_refs(tutor)) for tutor in pending
        ]

    if not snapshot:
        logger.info("Every tutor is already archived")
        return report

    storage = None
    if not dry_run:
        bot = get_bot()
        if bot is None:
            logger.error("No bot available — set BOT_TOKEN before backfilling")
            return report
        storage = TelegramStorageService(bot)

    for tdr_id, refs in snapshot:
        postable, damaged = usable_refs(refs)
        if damaged:
            report.damaged[tdr_id] = damaged
        if dry_run:
            logger.info(
                "Would archive %s with %s document(s)%s",
                tdr_id,
                len(postable),
                f"; unusable: {', '.join(damaged)}" if damaged else "",
            )
            continue

        result = await storage.store_tutor_record(tdr_id, postable or None)
        if result.success:
            report.archived.append(tdr_id)
        else:
            report.failed.append(tdr_id)
            logger.error("Could not archive %s: %s", tdr_id, result.error)

    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.backfill_archive",
        description="Archive tutors that were stored but never posted to Telegram.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be archived without sending anything.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging()
    report = asyncio.run(run_backfill(dry_run=args.dry_run))

    if report.dry_run:
        logger.info("Dry run — nothing was sent. Re-run without --dry-run to archive.")
    else:
        logger.info("Archived %s of %s tutor(s)", len(report.archived), len(report.archived) + len(report.failed))
    if report.failed:
        logger.error("Failed: %s", ", ".join(report.failed))
    for tdr_id, damaged in report.damaged.items():
        logger.warning(
            "%s has placeholder file references that cannot be archived: %s "
            "— ask the tutor to re-upload",
            tdr_id,
            ", ".join(damaged),
        )
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())