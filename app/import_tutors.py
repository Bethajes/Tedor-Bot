"""Bulk tutor importer (Requirements 11.1 – 11.4).

Usage::

    python -m app.import_tutors --file tutors.csv
    python -m app.import_tutors --file tutors.json --dry-run
    python -m app.import_tutors --file tutors.csv --with-telegram --status VERIFIED
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import init_db, session_scope
from app.enums import TutorStatus
from app.logging_config import configure_logging
from app.models import Tutor
from app.services import tutor_service

logger = logging.getLogger(__name__)

MULTI_VALUE_SEPARATORS = (";", "|", ",")

REQUIRED_FIELDS = (
    "full_name",
    "phone",
    "email",
    "country",
    "city",
    "teaching_mode",
)

FIELD_ALIASES = {
    "name": "full_name",
    "fullname": "full_name",
    "full_name": "full_name",
    "display_name": "display_name",
    "displayname": "display_name",
    "telegram_user_id": "telegram_user_id",
    "telegram_id": "telegram_user_id",
    "telegramid": "telegram_user_id",
    "user_id": "telegram_user_id",
    "phone": "phone",
    "phone_number": "phone",
    "mobile": "phone",
    "email": "email",
    "e_mail": "email",
    "email_address": "email",
    "country": "country",
    "city": "city",
    "bio": "bio",
    "biography": "bio",
    "subject": "subjects",
    "subjects": "subjects",
    "level": "levels",
    "levels": "levels",
    "language": "languages",
    "languages": "languages",
    "teaching_mode": "teaching_mode",
    "mode": "teaching_mode",
    "experience": "experience_years",
    "experience_years": "experience_years",
    "years_of_experience": "experience_years",
    "etb_rate": "etb_rate",
    "usd_rate": "usd_rate",
    "hourly_rate_etb": "etb_rate",
    "hourly_rate_usd": "usd_rate",
    "timezone": "timezone",
    "institution": "institution",
    "degree": "degree",
    "field": "field",
    "graduation_year": "graduation_year",
    "status": "status",
}


class RecordValidationError(ValueError):
    """Raised for a single invalid row (never aborts the whole import)."""


@dataclass
class ImportReport:
    """Per-row outcome summary (Requirement 11.1)."""

    total: int = 0
    created: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    invalid: list[tuple[int, str]] = field(default_factory=list)
    archived: list[str] = field(default_factory=list)
    dry_run: bool = False

    @property
    def created_count(self) -> int:
        return len(self.created)

    @property
    def skipped_count(self) -> int:
        return len(self.skipped)

    def summary(self) -> str:
        mode = "dry run" if self.dry_run else "import"
        return (
            f"{mode}: {self.total} row(s) — {self.created_count} created, "
            f"{self.skipped_count} skipped, {len(self.invalid)} invalid, "
            f"{len(self.archived)} archived to Telegram"
        )


# ---------------------------------------------------------------------------
# loading / normalisation
# ---------------------------------------------------------------------------


def load_records(path: str | Path) -> list[dict[str, Any]]:
    """Load a CSV or JSON file into a list of raw dictionaries."""
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Input file not found: {file_path}")
    suffix = file_path.suffix.lower()
    if suffix == ".json":
        payload = json.loads(file_path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            payload = payload.get("tutors") or payload.get("records") or []
        if not isinstance(payload, list):
            raise ValueError("JSON file must contain a list of records")
        return [dict(item) for item in payload]
    if suffix in {".csv", ".tsv"}:
        delimiter = "\t" if suffix == ".tsv" else ","
        with file_path.open(newline="", encoding="utf-8") as handle:
            return [dict(row) for row in csv.DictReader(handle, delimiter=delimiter)]
    raise ValueError(f"Unsupported file type: {suffix or file_path.name}")


def normalise_record(raw: dict[str, Any]) -> dict[str, Any]:
    """Map aliases and coerce types."""
    record: dict[str, Any] = {}
    for key, value in raw.items():
        if key is None:
            continue
        canonical = FIELD_ALIASES.get(str(key).strip().lower().replace(" ", "_"))
        if canonical is None:
            continue
        if isinstance(value, str):
            value = value.strip()
        record[canonical] = value
    for field_name in ("subjects", "levels", "languages"):
        if field_name in record:
            record[field_name] = split_multi(record[field_name])
    return record


def split_multi(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value)
    for separator in MULTI_VALUE_SEPARATORS:
        text = text.replace(separator, ",")
    return [part.strip() for part in text.split(",") if part.strip()]


def validate_record(record: dict[str, Any]) -> dict[str, Any]:
    """Validate one record, raising :class:`RecordValidationError` on failure."""
    missing = [name for name in REQUIRED_FIELDS if not record.get(name)]
    if missing:
        raise RecordValidationError("missing field(s): " + ", ".join(missing))

    teaching_mode = str(record["teaching_mode"]).upper()
    if teaching_mode not in {"ONLINE", "IN_PERSON", "BOTH"}:
        raise RecordValidationError(f"invalid teaching_mode: {record['teaching_mode']!r}")

    telegram_user_id = record.get("telegram_user_id")
    if telegram_user_id in (None, "", "None"):
        record["telegram_user_id"] = _synthetic_telegram_id(
            str(record["full_name"]), str(record["email"])
        )
    else:
        try:
            record["telegram_user_id"] = int(str(telegram_user_id).strip())
        except ValueError:
            raise RecordValidationError(
                f"telegram_user_id must be numeric, got {telegram_user_id!r}"
            ) from None

    for numeric in ("etb_rate", "usd_rate"):
        if record.get(numeric) not in (None, ""):
            try:
                record[numeric] = float(record[numeric])
            except (TypeError, ValueError):
                raise RecordValidationError(f"{numeric} must be a number") from None

    try:
        record["experience_years"] = int(record.get("experience_years") or 0)
    except (TypeError, ValueError):
        raise RecordValidationError("experience_years must be a whole number") from None

    status = str(record.get("status") or TutorStatus.VERIFIED).upper()
    if status not in {item.value for item in TutorStatus}:
        raise RecordValidationError(f"invalid status: {status!r}")
    record["status"] = status
    record["teaching_mode"] = teaching_mode
    if not record.get("display_name"):
        record["display_name"] = str(record["full_name"])
    return record


def _synthetic_telegram_id(full_name: str, email: str) -> int:
    """Stable negative ID so legacy rows can be imported and de-duplicated.

    Uses a digest instead of :func:`hash` so repeated runs (and separate
    processes) always produce the same value — required by Property 10.
    """
    digest = int(
        hashlib.md5(f"{full_name.casefold()}|{email.casefold()}".encode()).hexdigest()[:16],
        16,
    )
    return -(digest % 9_000_000_000) - 1


def to_tutor_payload(record: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "telegram_user_id": record["telegram_user_id"],
        "name": record["full_name"],
        "display_name": record.get("display_name") or record["full_name"],
        "phone": record.get("phone", ""),
        "email": record.get("email", ""),
        "country": record["country"],
        "city": record["city"],
        "bio": record.get("bio", ""),
        "profile_photo_file_id": record.get("profile_photo_file_id"),
        "etb_rate": record.get("etb_rate"),
        "usd_rate": record.get("usd_rate"),
        "experience_years": record.get("experience_years", 0),
        "timezone": record.get("timezone", "UTC"),
        "teaching_mode": record["teaching_mode"],
        "status": record.get("status", str(TutorStatus.VERIFIED)),
        "subjects": record.get("subjects") or [],
        "levels": record.get("levels") or [],
        "languages": record.get("languages") or [],
    }
    if record.get("institution"):
        payload["education"] = [
            {
                "institution": record.get("institution", ""),
                "degree": record.get("degree", ""),
                "field": record.get("field", ""),
                "graduation_year": _as_int(record.get("graduation_year")),
            }
        ]
    return payload


def _as_int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# duplicate detection (Requirement 11.4)
# ---------------------------------------------------------------------------


def find_duplicate(session: Session, record: dict[str, Any]) -> str | None:
    """Return a description of the duplicate, or ``None``."""
    telegram_user_id = record.get("telegram_user_id")
    if telegram_user_id:
        existing = tutor_service.get_tutor_by_telegram_id(session, int(telegram_user_id))
        if existing is not None:
            return f"duplicate telegram user id (existing {existing.public_tutor_id})"
    name = str(record.get("full_name") or "").strip().casefold()
    email = str(record.get("email") or "").strip().casefold()
    if name and email:
        existing = session.execute(
            select(Tutor).where(
                Tutor.name.ilike(name),
                Tutor.email.ilike(email),
            )
        ).scalars().first()
        if existing is not None:
            return f"duplicate name+email (existing {existing.public_tutor_id})"
    return None


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------


def import_records(
    records: list[dict[str, Any]],
    *,
    dry_run: bool = False,
    storage: Any | None = None,
) -> ImportReport:
    """Import raw records, reporting every row (Property 10: idempotent)."""
    report = ImportReport(total=len(records), dry_run=dry_run)

    for index, raw in enumerate(records, start=1):
        try:
            record = validate_record(normalise_record(raw))
        except RecordValidationError as exc:
            report.invalid.append((index, str(exc)))
            logger.warning("Row %s rejected: %s", index, exc)
            continue

        with session_scope() as session:
            duplicate = find_duplicate(session, record)
            if duplicate is not None:
                report.skipped.append((str(record.get("full_name", f"row {index}")), duplicate))
                logger.warning("Row %s skipped: %s", index, duplicate)
                continue
            if dry_run:
                report.created.append(f"(dry run) {record['full_name']}")
                continue
            tutor = tutor_service.create_tutor(session, to_tutor_payload(record))
            tdr_id = tutor.public_tutor_id
        report.created.append(f"{record['full_name']} -> {tdr_id}")

    if storage is not None and not dry_run and report.created:
        asyncio.run(_archive_created(storage, report))
    return report


async def _archive_created(storage: Any, report: ImportReport) -> None:
    for entry in report.created:
        if "->" not in entry:
            continue
        tdr_id = entry.split("->")[-1].strip()
        result = await storage.store_tutor_record(tdr_id)
        if result.success:
            report.archived.append(tdr_id)
        else:  # pragma: no cover - network dependent
            logger.error("Archiving failed for %s", tdr_id)


def build_storage_service() -> Any | None:
    """Create the Telegram storage service when a token is configured."""
    from app.config import settings

    if not settings.bot_token:
        logger.error("BOT_TOKEN is not set — Telegram archiving disabled")
        return None
    from telegram import Bot

    from app.services.storage_service import TelegramStorageService

    return TelegramStorageService(Bot(settings.bot_token))


def run_import(
    path: str | Path,
    *,
    dry_run: bool = False,
    with_telegram: bool = False,
) -> ImportReport:
    """Load, validate and import a file, returning the report."""
    init_db()
    records = load_records(path)
    storage = build_storage_service() if with_telegram and not dry_run else None
    return import_records(records, dry_run=dry_run, storage=storage)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.import_tutors",
        description="Import existing tutor records into the Tedor Tutors SQLite index.",
    )
    parser.add_argument("--file", required=True, help="Path to a .csv, .tsv or .json file")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and report without writing anything",
    )
    parser.add_argument(
        "--with-telegram",
        action="store_true",
        help="Also send a storage record to the Telegram archive channel",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = build_parser().parse_args(argv)
    try:
        report = run_import(args.file, dry_run=args.dry_run, with_telegram=args.with_telegram)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Import failed: {exc}", file=sys.stderr)
        return 2

    for tdr_id in report.created:
        print(f"  created  {tdr_id}")
    for name, reason in report.skipped:
        print(f"  skipped  {name} — {reason}")
    for index, reason in report.invalid:
        print(f"  invalid  row {index} — {reason}")
    print(report.summary())
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())