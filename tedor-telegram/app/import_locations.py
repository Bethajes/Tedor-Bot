"""Import the curated location channel's history into the database.

The Bot API has no method that returns a channel's message history, so a bot
cannot backfill posts that were made before it became an administrator of the
channel. The history therefore arrives as a file: an administrator opens the
channel in Telegram Desktop, chooses *Export channel history*, picks **JSON**
(not HTML), and this command reads the result.

    python -m app.import_locations --file result.json
    python -m app.import_locations --file result.json --dry-run
    python -m app.import_locations --file result.json --reconcile

``--dry-run`` parses and validates every record and prints what would happen
without writing anything. ``--reconcile`` additionally deactivates channel
records whose message is absent from the export, which is the only way a
deleted post can be noticed: Telegram sends no update when a message is
removed. Nothing is ever deleted — a deactivated place keeps its row because
tutors and published jobs already point at it.

Posts made *after* the bot joined the channel need no export: they are handled
live by ``app.bot.handlers.location_channel``.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from app.config import settings
from app.database import init_db, session_scope
from app.logging_config import configure_logging
from app.services import location_channel_service as channel

logger = logging.getLogger(__name__)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.import_locations",
        description="Import curated tutoring locations from a channel export.",
    )
    parser.add_argument(
        "--file",
        required=True,
        type=Path,
        help="Path to the channel's result.json (Telegram Desktop JSON export).",
    )
    parser.add_argument(
        "--chat-id",
        type=int,
        default=None,
        help="Channel id. Defaults to LOCATION_CHANNEL_ID from the environment.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and report without writing anything.",
    )
    parser.add_argument(
        "--reconcile",
        action="store_true",
        help=(
            "Deactivate channel records missing from the export. Use only on a "
            "complete export of the channel."
        ),
    )
    return parser.parse_args(argv)


def _dry_run(path: Path, chat_id: int) -> int:
    """Parse every record and report, writing nothing."""
    accepted = 0
    rejected: list[str] = []
    for message_id, text, _posted_at in channel.iter_export_messages(path):
        try:
            record = channel.parse_record(text, chat_id=chat_id, message_id=message_id)
        except channel.RecordError as exc:
            rejected.append(f"message {message_id}: {exc.user_message}")
            continue
        accepted += 1
        coordinates = (
            f"{record.latitude}, {record.longitude}"
            if record.latitude is not None and record.longitude is not None
            else "no coordinates"
        )
        print(
            f"  {record.id}  {record.name}  "
            f"[{record.status}, {coordinates}]"
            + (f"  aliases: {', '.join(record.aliases)}" if record.aliases else "")
        )
    print(f"\nWould import {accepted} record(s); {len(rejected)} rejected.")
    for reason in rejected:
        print(f"  ✗ {reason}")
    print("\nThis was a dry run. Nothing was written.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    configure_logging(settings.log_level)

    chat_id = args.chat_id or settings.location_channel_id
    if not chat_id:
        print(
            "No channel id. Set LOCATION_CHANNEL_ID or pass --chat-id "
            "(channel ids look like -1001234567890).",
            file=sys.stderr,
        )
        return 2

    if not args.file.exists():
        print(f"No such file: {args.file}", file=sys.stderr)
        return 2

    print(f"Reading {args.file} for channel {chat_id}\n")

    if args.dry_run:
        return _dry_run(args.file, chat_id)

    init_db()
    with session_scope() as session:
        summary = channel.import_export_file(
            session, args.file, chat_id=chat_id, reconcile_missing=args.reconcile
        )
    # Reported only after the session scope has committed, so a failed commit
    # cannot be announced as a successful import.
    print(channel.format_summary(summary).replace("<b>", "").replace("</b>", ""))

    print(
        "\nDone. New and edited channel posts are now imported automatically as "
        "they happen."
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())