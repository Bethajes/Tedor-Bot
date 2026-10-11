"""Admin deletion, re-application afterwards, and tutor self-service editing.

Three guarantees this covers:

* an admin can remove a tutor completely, leaving nothing behind in the
  database, and the tutor can then apply again from scratch;
* a tutor can correct an answer they already submitted, without an admin, and
  without that edit touching their verification status or documents.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.orm import Session
from telegram.ext import ConversationHandler

from app.bot.handlers import admin as adm
from app.bot.handlers import tutor_onboarding as eth
from app.bot.keyboards.admin import CB_ADMIN_DELETE, CB_ADMIN_DELETE_CONFIRM
from app.bot.states import EthiopianTutorStates
from app.database import session_scope
from app.models import Draft, PendingDocument, TutorDocument, TutorGrade, TutorSubject
from app.services import tutor_profile_service as P
from app.services import tutor_service
from tests.factories import tutor_payload
from tests.test_enrichment_flow import _complete_tutor
from tests.test_ethiopian_onboarding import FakeContext, drive

#: ``drive`` builds its updates with the default test user, so the tutor
#: under test has to be that user.
TELEGRAM_ID = 4242


def _stored(session, telegram_id: int = TELEGRAM_ID):
    return _complete_tutor(session)


def _by_id(session, telegram_id: int = TELEGRAM_ID):
    return tutor_service.get_tutor_by_telegram_id(session, telegram_id)


# ---------------------------------------------------------------------------
# Deleting a tutor
# ---------------------------------------------------------------------------


def test_deleting_removes_the_tutor_and_every_row_under_it(db: Session) -> None:
    tutor = _stored(db)

    report = tutor_service.delete_tutor(db, tutor.public_tutor_id)
    db.commit()

    assert report is not None
    assert report.tdr_id == tutor.public_tutor_id
    assert _by_id(db) is None
    assert db.query(TutorSubject).filter_by(tutor_id=tutor.id).count() == 0
    assert db.query(TutorGrade).filter_by(tutor_id=tutor.id).count() == 0
    assert db.query(TutorDocument).filter_by(tutor_id=tutor.id).count() == 0


def test_deleting_an_unknown_reference_reports_nothing(db: Session) -> None:
    assert tutor_service.delete_tutor(db, "TDR-999999") is None


def test_the_report_counts_documents_even_on_a_stale_session(db: Session) -> None:
    """The session does not expire on commit, so a cached collection lies.

    Reading ``tutor.documents`` here would report zero documents removed and
    leave the admin thinking nothing was attached.
    """
    tutor = _stored(db)
    tutor_service.record_document_reference(
        db, tutor, "CV", file_id="f" * 40, message_id=9, chat_id=-100
    )
    db.commit()

    report = tutor_service.delete_tutor(db, tutor.public_tutor_id)
    db.commit()

    assert report.cascaded_documents >= 1


def test_the_report_names_the_channel_messages_that_remain(db: Session) -> None:
    """Deleting the record does not silently delete the audit trail."""
    tutor = _stored(db)
    tutor_service.set_storage_references(db, tutor, -100123, 55)
    tutor_service.record_document_reference(
        db, tutor, "CV", file_id="f" * 40, message_id=56, chat_id=-100123
    )
    db.commit()

    report = tutor_service.delete_tutor(db, tutor.public_tutor_id)
    db.commit()

    assert "record message 55" in " ".join(report.archived_messages)
    assert "CV message 56" in " ".join(report.archived_messages)


def test_a_staged_document_does_not_attach_to_the_next_application(
    db: Session,
) -> None:
    """Staged rows are keyed by user, not by tutor, so they need clearing too.

    Otherwise a tutor who re-applies silently inherits the transcript they
    uploaded to the application that was just deleted.
    """
    tutor = _stored(db)
    db.add(
        PendingDocument(
            telegram_user_id=tutor.telegram_user_id,
            document_type="UNIVERSITY_TRANSCRIPT",
            file_name="old.pdf",
        )
    )
    db.add(
        Draft(
            telegram_user_id=tutor.telegram_user_id,
            draft_type="tutor",
            payload="{}",
        )
    )
    db.commit()

    tutor_service.delete_tutor(db, tutor.public_tutor_id)
    db.commit()

    assert db.query(PendingDocument).count() == 0
    assert db.query(Draft).count() == 0


def test_deleting_one_tutor_leaves_the_others_alone(db: Session) -> None:
    keep = tutor_service.create_tutor(db, tutor_payload(9101, name="Keeper"))

    tutor_service.delete_tutor(db, _stored(db).public_tutor_id)
    db.commit()

    assert tutor_service.get_tutor_by_tdr_id(db, keep.public_tutor_id) is not None


# ---------------------------------------------------------------------------
# Applying again after a deletion
# ---------------------------------------------------------------------------


def test_a_deleted_tutor_can_apply_again_with_a_fresh_reference(db: Session) -> None:
    old = _stored(db)
    old_id = old.public_tutor_id

    tutor_service.delete_tutor(db, old_id)
    db.commit()

    fresh = P.create_ethiopian_tutor(
        db,
        {
            "telegram_user_id": TELEGRAM_ID,
            "full_name": "Abebe Bekele",
            "phone": "+251911234567",
            "locations": ["Ayat Tsebel"],
            "grades": ["Grade 4"],
            "subjects": ["Mathematics"],
        },
    )
    db.commit()

    assert _by_id(db) is not None
    assert fresh.public_tutor_id != old_id, "a TDR ID must never be reused"


def test_the_deleted_application_does_not_block_the_new_one(db: Session) -> None:
    """The Mini App refuses a second application with HTTP 409.

    That guard reads the tutor row, so it has to stop blocking once the row is
    gone — otherwise the tutor could delete and still never re-apply.
    """
    tutor_service.delete_tutor(db, _stored(db).public_tutor_id)
    db.commit()

    assert tutor_service.get_tutor_by_telegram_id(db, TELEGRAM_ID) is None


# ---------------------------------------------------------------------------
# Editing after submitting
# ---------------------------------------------------------------------------


def test_every_editable_field_maps_to_a_conversation_step() -> None:
    for field in eth.EDITABLE_FIELDS:
        assert eth._field_states(field), f"{field} has no step to run"


def test_status_and_documents_are_not_tutor_editable() -> None:
    """Verification is the admin's decision; documents are their own flow."""
    assert "status" not in eth.EDITABLE_FIELDS
    assert "documents" not in eth.EDITABLE_FIELDS


def test_editing_the_entrance_exam_reasks_the_whole_chain() -> None:
    """A score is meaningless without its scale, so the chain moves together."""
    states = eth._field_states("entrance_exam_score")

    assert states == [int(state) for state in eth._ENTRANCE_CHAIN]
    assert len(states) > 1


def test_the_entrance_chain_is_offered_once_not_three_times() -> None:
    """education level, stream and score are one button, not three."""
    labels = [
        eth._edit_label(field)
        for field in eth.EDITABLE_FIELDS
        if eth._field_states(field) == eth._field_states("entrance_exam_score")
    ]
    assert len(labels) == 1


def test_the_edit_menu_shows_the_current_value(db: Session) -> None:
    tutor = _stored(db)
    tutor.phone = "+251911234567"
    db.commit()

    assert eth._current_value(tutor, "phone") == "+251911234567"
    assert eth._current_value(tutor, "subjects") == "Mathematics"
    assert eth._current_value(tutor, "entrance_exam_score") == "612/840"


def test_an_empty_field_reads_as_a_dash(db: Session) -> None:
    tutor = _stored(db)
    tutor.department = ""
    db.commit()

    assert eth._current_value(tutor, "department") == "—"


async def test_a_tutor_can_change_their_phone(db: Session) -> None:
    _stored(db)
    conversation = eth.build_conversation()
    context = FakeContext()

    state = await drive(
        conversation,
        context,
        [
            ("command", "edit_profile"),
            ("callback", f"{eth.EDIT_CALLBACK}phone"),
            ("text", "+251900000111"),
            ("callback", "eth:submit"),
        ],
        start=False,
    )

    with session_scope() as session:
        tutor = _by_id(session)
        assert tutor.phone == "+251900000111"
        # Editing a detail must not disturb what the admin decided.
        assert tutor.status == "PENDING"


async def test_a_tutor_can_change_their_name(db: Session) -> None:
    _stored(db)
    conversation = eth.build_conversation()
    context = FakeContext()

    await drive(
        conversation,
        context,
        [
            ("command", "edit_profile"),
            ("callback", f"{eth.EDIT_CALLBACK}full_name"),
            ("text", "Abebe Bekele Kebede"),
            ("callback", "eth:submit"),
        ],
        start=False,
    )

    with session_scope() as session:
        tutor = _by_id(session)
        assert tutor.name == "Abebe Bekele Kebede"
        # This tutor's display name was never derived from their full name, so
        # correcting the legal name must not rename them publicly.
        assert tutor.display_name == "Tutor 4242"


async def test_editing_one_field_leaves_the_others_alone(db: Session) -> None:
    _stored(db)
    conversation = eth.build_conversation()
    context = FakeContext()

    await drive(
        conversation,
        context,
        [
            ("command", "edit_profile"),
            ("callback", f"{eth.EDIT_CALLBACK}phone"),
            ("text", "+251900000222"),
            ("callback", "eth:submit"),
        ],
        start=False,
    )

    with session_scope() as session:
        tutor = _by_id(session)
        assert tutor.phone == "+251900000222"
        assert tutor.subject_list == ["Mathematics"]
        assert tutor.name == "Abebe Bekele"


async def test_an_invalid_answer_is_refused_and_nothing_is_saved(
    db: Session,
) -> None:
    _stored(db)
    conversation = eth.build_conversation()
    context = FakeContext()

    await drive(
        conversation,
        context,
        [
            ("command", "edit_profile"),
            ("callback", f"{eth.EDIT_CALLBACK}phone"),
            ("text", "call me maybe"),
            ("callback", "eth:submit"),
        ],
        start=False,
    )

    with session_scope() as session:
        assert _by_id(session).phone == "+251911234567"


async def test_editing_without_a_profile_is_refused(db: Session) -> None:
    """Nothing to edit, so the tutor is sent to apply instead."""
    conversation = eth.build_conversation()
    context = FakeContext()

    state = await drive(
        conversation, context, [("command", "edit_profile")], start=False
    )

    assert state == ConversationHandler.END


async def test_an_unknown_field_is_refused(db: Session) -> None:
    _stored(db)
    conversation = eth.build_conversation()
    context = FakeContext()

    state = await drive(
        conversation,
        context,
        [
            ("command", "edit_profile"),
            ("callback", f"{eth.EDIT_CALLBACK}status"),
        ],
        start=False,
    )

    assert state == int(EthiopianTutorStates.SUMMARY)
    with session_scope() as session:
        assert _by_id(session).status == "PENDING"


def test_a_chosen_display_name_survives_a_legal_name_change(db: Session) -> None:
    tutor = _stored(db)
    tutor.display_name = "Becky B."
    db.commit()

    P.update_profile_fields(db, tutor, {"full_name": "Bekelech Tadesse"})
    db.commit()

    assert tutor.name == "Bekelech Tadesse"
    assert tutor.display_name == "Becky B."


def test_an_invalid_phone_is_refused_by_the_service(db: Session) -> None:
    tutor = _stored(db)

    with pytest.raises(P.ProfileValidationError):
        P.update_profile_fields(db, tutor, {"phone": "not-a-phone"})


# ---------------------------------------------------------------------------
# The admin buttons
# ---------------------------------------------------------------------------


def test_the_tutor_detail_offers_a_delete_button() -> None:
    from app.bot.keyboards.admin import status_button_keyboard

    labels = [
        button.text
        for row in status_button_keyboard("TDR-000001").inline_keyboard
        for button in row
    ]
    assert any("Delete" in label for label in labels)


def test_the_delete_button_only_asks_first() -> None:
    """The first callback must not be able to destroy anything on its own.

    The detail view offers ``admin:delete:<id>``, which only asks; the confirm
    screen is the one that carries ``admin:delete_confirm:<id>``.
    """
    from app.bot.keyboards.admin import delete_confirm_keyboard, status_button_keyboard

    detail = [
        button.callback_data
        for row in status_button_keyboard("TDR-000001").inline_keyboard
        for button in row
    ]
    confirm = [
        button.callback_data
        for row in delete_confirm_keyboard("TDR-000001", "Bethel").inline_keyboard
        for button in row
    ]

    assert f"{CB_ADMIN_DELETE}TDR-000001" in detail
    assert f"{CB_ADMIN_DELETE_CONFIRM}TDR-000001" not in detail
    assert f"{CB_ADMIN_DELETE_CONFIRM}TDR-000001" in confirm
    # The two prefixes must not shadow one another, or the confirming tap would
    # be handled by the asking branch.
    assert f"{CB_ADMIN_DELETE_CONFIRM}TDR-1".startswith(CB_ADMIN_DELETE) is False


async def test_a_non_admin_cannot_reach_the_delete_handlers(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    tutor = _stored(db)
    monkeypatch.setattr(adm.settings, "admin_user_ids", [])
    update = _admin_update(999_999)

    assert await adm._confirm_delete_tutor(update, FakeContext(), tutor.public_tutor_id) == (
        ConversationHandler.END
    )
    assert await adm._delete_tutor(update, FakeContext(), tutor.public_tutor_id) == (
        ConversationHandler.END
    )

    with session_scope() as session:
        assert _by_id(session) is not None


def _admin_update(user_id: int = 111) -> Any:
    """A message-shaped update that ``require_admin`` and ``safe_reply`` accept."""
    from tests.test_admin_dashboard import _update

    return _update(user_id=user_id)


async def test_confirming_a_delete_removes_the_tutor(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    tutor = _stored(db)
    monkeypatch.setattr(adm, "is_admin", lambda _user_id: True)
    context = FakeContext()

    await adm._delete_tutor(_admin_update(1234), context, tutor.public_tutor_id)

    with session_scope() as session:
        assert _by_id(session) is None