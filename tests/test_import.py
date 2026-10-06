"""Bulk importer tests (Requirement 11.x).

Feature: tedor-telegram-bot, Property 10: Import idempotency
Validates: Requirements 11.4
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings as hypothesis_settings, strategies as st
from sqlalchemy import func, select

from app import import_tutors
from app.models import Tutor
from app.services import tutor_service

PROFILE = hypothesis_settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


def _records(count: int, seed: int = 1) -> list[dict]:
    records = []
    for index in range(count):
        telegram_id = 900_000 + seed * 1000 + index
        records.append(
            {
                "full_name": f"Tutor {seed}-{index}",
                "display_name": f"T{index}",
                "telegram_user_id": telegram_id,
                "phone": f"+25191{index:07d}",
                "email": f"tutor{seed}-{index}@example.com",
                "country": "Ethiopia",
                "city": "Addis Ababa",
                "subjects": "Mathematics;Physics",
                "levels": "Grade 9-10",
                "languages": "English",
                "teaching_mode": "ONLINE",
                "experience_years": "3",
                "etb_rate": "350",
                "usd_rate": "3.5",
            }
        )
    return records


def _write_json(tmp_path: Path, records: list[dict]) -> Path:
    path = tmp_path / "tutors.json"
    path.write_text(json.dumps(records), encoding="utf-8")
    return path


def _write_csv(tmp_path: Path, records: list[dict]) -> Path:
    path = tmp_path / "tutors.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0].keys()))
        writer.writeheader()
        writer.writerows(records)
    return path


def _count_tutors(session) -> int:
    return int(session.execute(select(func.count()).select_from(Tutor)).scalar_one())


# ---------------------------------------------------------------------------
# Property 10
# ---------------------------------------------------------------------------


@given(
    count=st.integers(min_value=1, max_value=5),
    seed=st.integers(min_value=1, max_value=10**4),
)
@PROFILE
def test_property_import_idempotency(
    fresh_db, tmp_path: Path, count: int, seed: int
) -> None:
    """Feature: tedor-telegram-bot, Property 10: Import idempotency.

    Running the same file twice must not create duplicate tutors.
    """
    session = fresh_db()
    path = _write_json(tmp_path, _records(count, seed))

    first = import_tutors.run_import(path)
    session.expire_all()
    total_after_first = _count_tutors(session)
    assert first.created_count == count
    assert total_after_first == count

    second = import_tutors.run_import(path)
    session.expire_all()
    total_after_second = _count_tutors(session)
    assert total_after_second == total_after_first
    assert second.created_count == 0
    assert second.skipped_count == count


def test_dry_run_writes_nothing(fresh_db, tmp_path: Path) -> None:
    """Requirement 11.3."""
    session = fresh_db()
    path = _write_json(tmp_path, _records(3))

    report = import_tutors.run_import(path, dry_run=True)
    session.expire_all()
    assert report.dry_run is True
    assert report.created_count == 3
    assert _count_tutors(session) == 0
    assert "dry run" in report.summary()


def test_csv_import_creates_records(fresh_db, tmp_path: Path) -> None:
    session = fresh_db()
    path = _write_csv(tmp_path, _records(4))
    report = import_tutors.run_import(path)
    session.expire_all()
    assert report.created_count == 4
    assert _count_tutors(session) == 4
    tutor = session.execute(select(Tutor)).scalars().first()
    assert tutor.status == "VERIFIED"
    assert sorted(tutor.subject_list) == ["Mathematics", "Physics"]


def test_invalid_rows_are_reported_without_stopping(fresh_db, tmp_path: Path) -> None:
    """Requirement 11.1."""
    session = fresh_db()
    records = _records(3)
    records.insert(1, {"full_name": "Broken", "country": "Kenya"})
    extra = {**records[0], "telegram_user_id": 424242, "email": "other@example.com"}
    records.append(extra)
    path = _write_json(tmp_path, records)

    report = import_tutors.run_import(path)
    session.expire_all()
    assert report.created_count == 4
    assert len(report.invalid) == 1
    assert report.invalid[0][1].startswith("missing field")
    assert _count_tutors(session) == 4


def test_duplicate_telegram_id_is_skipped(fresh_db, tmp_path: Path) -> None:
    """Requirement 11.4."""
    session = fresh_db()
    records = _records(2)
    path = _write_json(tmp_path, records)
    import_tutors.run_import(path)
    session.expire_all()

    duplicated = records + [records[0]]
    second_path = _write_json(tmp_path, duplicated)
    report = import_tutors.run_import(second_path)
    session.expire_all()
    assert report.skipped_count == 3
    assert all("duplicate telegram user id" in reason for _name, reason in report.skipped)
    assert _count_tutors(session) == 2


def test_duplicate_name_and_email_is_skipped(fresh_db, tmp_path: Path) -> None:
    session = fresh_db()
    records = _records(1)
    path = _write_json(tmp_path, records)
    import_tutors.run_import(path)
    session.expire_all()

    twin = dict(records[0])
    twin["telegram_user_id"] = 987_654
    second_path = _write_json(tmp_path, [twin])
    report = import_tutors.run_import(second_path)
    session.expire_all()
    assert report.skipped_count == 1
    assert "duplicate name+email" in report.skipped[0][1]
    assert _count_tutors(session) == 1


def test_legacy_rows_without_telegram_id_are_deduplicated(fresh_db, tmp_path: Path) -> None:
    session = fresh_db()
    records = _records(2)
    for record in records:
        record.pop("telegram_user_id")
    path = _write_json(tmp_path, records)

    first = import_tutors.run_import(path)
    second = import_tutors.run_import(path)
    session.expire_all()
    assert first.created_count == 2
    assert second.created_count == 0
    assert _count_tutors(session) == 2


def test_import_assigns_tdr_ids_in_sequence(fresh_db, tmp_path: Path) -> None:
    fresh_db()
    path = _write_json(tmp_path, _records(3))
    report = import_tutors.run_import(path)
    ids = [entry.split("->")[-1].strip() for entry in report.created]
    assert len(set(ids)) == 3
    for tdr_id in ids:
        assert tutor_service.TDR_PATTERN.match(tdr_id)
    sequences = [tutor_service.tdr_sequence(tdr_id) for tdr_id in ids]
    assert sequences == sorted(sequences)


def test_unsupported_file_type_raises(tmp_path: Path) -> None:
    path = tmp_path / "tutors.txt"
    path.write_text("nope", encoding="utf-8")
    with pytest.raises(ValueError):
        import_tutors.load_records(path)


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        import_tutors.load_records(tmp_path / "absent.json")


def test_cli_reports_summary(fresh_db, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    path = _write_json(tmp_path, _records(2))
    exit_code = import_tutors.main(["--file", str(path), "--dry-run"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "dry run" in captured.out
    assert "created" in captured.out


def test_cli_reports_failure_for_bad_path(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    exit_code = import_tutors.main(["--file", str(tmp_path / "missing.json")])
    assert exit_code == 2
    assert "Import failed" in capsys.readouterr().err


def test_invalid_status_is_rejected() -> None:
    record = {
        "full_name": "A B",
        "phone": "+251911000000",
        "email": "a@example.com",
        "country": "Ethiopia",
        "city": "Addis Ababa",
        "teaching_mode": "ONLINE",
        "status": "BANANA",
    }
    with pytest.raises(import_tutors.RecordValidationError):
        import_tutors.validate_record(record)


def test_invalid_teaching_mode_is_rejected() -> None:
    record = {
        "full_name": "A B",
        "phone": "+251911000000",
        "email": "a@example.com",
        "country": "Ethiopia",
        "city": "Addis Ababa",
        "teaching_mode": "CARRIER_PIGEON",
    }
    with pytest.raises(import_tutors.RecordValidationError):
        import_tutors.validate_record(record)