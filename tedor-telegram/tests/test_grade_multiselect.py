"""Multi-select grade level for student tutor requests.

Covers the UI (toggle + Done, typed fallback), the comma-joined storage, and
the band-aware matching in :mod:`app.services.search_service`.
"""

from __future__ import annotations

import pytest

from app.bot.handlers import student
from app.bot.options import GRADE_LEVELS
from app.services import search_service, student_service
from tests.factories import student_request_payload
from tests.test_conversation_flow import (
    FakeContext,
    callback_update,
    dispatch,
    text_update,
)


def _level_step():
    return next(s for s in student.STEPS if s.field == "education_level")


# ---------------------------------------------------------------------------
# the option list
# ---------------------------------------------------------------------------


def test_grade_levels_include_individual_grades_and_the_bands() -> None:
    assert "KG" in GRADE_LEVELS
    for grader in ("1st Grader", "4th Grader", "9th Grader", "12th Grader"):
        assert grader in GRADE_LEVELS
    # the coarse bands are still available for a whole-range request
    for band in ("Grade 1-3", "Grade 7-8", "Grade 9-10", "Grade 11-12"):
        assert band in GRADE_LEVELS
    assert "University" in GRADE_LEVELS
    assert "All Levels" in GRADE_LEVELS
    assert len(GRADE_LEVELS) == len(set(GRADE_LEVELS))


def test_the_education_level_step_is_a_multi_select() -> None:
    step = next(s for s in student.STEPS if s.field == "education_level")
    assert step.kind == "multiselect"
    assert step.options == GRADE_LEVELS
    assert "education_level" in student.MULTISELECT_FIELDS


def test_the_tutor_signup_bands_are_untouched() -> None:
    """Only the request side changed granularity."""
    from app.bot.options import LEVELS

    assert "Grade 7-8" in LEVELS
    assert "9th Grader" not in LEVELS


def test_the_multi_select_keyboard_uses_the_student_namespace() -> None:
    step = next(s for s in student.STEPS if s.field == "education_level")
    keyboard = student._keyboard_for(step, {})
    data = [
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
    ]
    assert "student:education_level:done" in data
    assert "student:back" in data
    assert "student:cancel" in data
    # the pre-fix keyboard emitted the tutor namespace here
    assert "tutor:back" not in data
    assert "tutor:cancel" not in data
    for item in data:
        assert len(item.encode("utf-8")) <= 64, item


# ---------------------------------------------------------------------------
# the conversation
# ---------------------------------------------------------------------------


async def _reach_level_step(context: FakeContext):
    """Drive /find_tutor up to the education-level multi-select."""
    conversation = student.build_student_conversation()
    bot = context.bot
    state = await dispatch(
        conversation, callback_update("menu:find_tutor", bot=bot), context, None
    )
    state = await dispatch(conversation, text_update("Asnake", bot=bot), context, state)
    state = await dispatch(conversation, text_update("9th", bot=bot), context, state)
    state = await dispatch(
        conversation, callback_update("student:country:pick:0", bot=bot), context, state
    )
    assert state == int(student.StudentStates.EDUCATION_LEVEL)
    return conversation, state


@pytest.mark.asyncio
async def test_several_levels_can_be_toggled(api_env) -> None:
    context = FakeContext()
    conversation, state = await _reach_level_step(context)
    bot = context.bot

    options = list(GRADE_LEVELS)
    for wanted in ("7th Grader", "Grade 7-8", "9th Grader"):
        state = await dispatch(
            conversation,
            callback_update(f"student:education_level:toggle:{options.index(wanted)}", bot=bot),
            context,
            state,
        )
        assert state == int(student.StudentStates.EDUCATION_LEVEL)

    assert context.user_data["student_request"]["education_level"] == [
        "7th Grader",
        "Grade 7-8",
        "9th Grader",
    ]

    state = await dispatch(
        conversation, callback_update("student:education_level:done", bot=bot), context, state
    )
    assert state == int(student.StudentStates.SUBJECT)


@pytest.mark.asyncio
async def test_toggling_the_same_level_twice_removes_it(api_env) -> None:
    context = FakeContext()
    conversation, state = await _reach_level_step(context)
    bot = context.bot

    options = list(GRADE_LEVELS)
    toggle = f"student:education_level:toggle:{options.index('9th Grader')}"
    state = await dispatch(conversation, callback_update(toggle, bot=bot), context, state)
    assert context.user_data["student_request"]["education_level"] == ["9th Grader"]
    state = await dispatch(conversation, callback_update(toggle, bot=bot), context, state)
    assert context.user_data["student_request"]["education_level"] == []


@pytest.mark.asyncio
async def test_done_without_a_selection_keeps_the_parent_on_the_step(api_env) -> None:
    context = FakeContext()
    conversation, state = await _reach_level_step(context)
    bot = context.bot

    state = await dispatch(
        conversation, callback_update("student:education_level:done", bot=bot), context, state
    )
    assert state == int(student.StudentStates.EDUCATION_LEVEL)
    assert any("at least one" in text for text in bot.sent)


@pytest.mark.asyncio
async def test_typed_levels_are_accepted_as_a_list(api_env) -> None:
    context = FakeContext()
    conversation, state = await _reach_level_step(context)
    bot = context.bot

    state = await dispatch(
        conversation,
        text_update("Grade 7-8, Grade 9-10", bot=bot),
        context,
        state,
    )
    assert state == int(student.StudentStates.SUBJECT)
    assert context.user_data["student_request"]["education_level"] == [
        "Grade 7-8",
        "Grade 9-10",
    ]


@pytest.mark.asyncio
async def test_the_summary_shows_every_level(api_env) -> None:
    context = FakeContext()
    conversation, state = await _reach_level_step(context)
    bot = context.bot

    options = list(GRADE_LEVELS)
    for wanted in ("7th Grader", "9th Grader"):
        state = await dispatch(
            conversation,
            callback_update(f"student:education_level:toggle:{options.index(wanted)}", bot=bot),
            context,
            state,
        )
    state = await dispatch(
        conversation, callback_update("student:education_level:done", bot=bot), context, state
    )

    # the selection is a list, so the summary must render every level
    payload = context.user_data["student_request"]
    assert payload["education_level"] == ["7th Grader", "9th Grader"]

    from app.bot.handlers.common import summary_lines

    rendered = "\n".join(summary_lines(payload, student.SUMMARY_LABELS))
    assert "7th Grader, 9th Grader" in rendered


# ---------------------------------------------------------------------------
# storage
# ---------------------------------------------------------------------------


def test_join_levels_flattens_a_list() -> None:
    assert student_service.join_levels(["7th Grader", "8th Grader"]) == "7th Grader, 8th Grader"
    assert student_service.join_levels("Grade 9-10") == "Grade 9-10"
    assert student_service.join_levels(None) == ""
    assert student_service.join_levels([]) == ""


def test_a_multi_level_request_is_stored_comma_joined(fresh_db) -> None:
    session = fresh_db()
    with __import__("app.database", fromlist=["session_scope"]).session_scope() as create:
        request = student_service.create_student_request(
            create,
            student_request_payload(
                -7001, education_level=["7th Grader", "Grade 7-8", "9th Grader"]
            ),
        )
        assert request.education_level == "7th Grader, Grade 7-8, 9th Grader"
        assert "Level: 7th Grader, Grade 7-8, 9th Grader" in (
            student_service.format_request_summary(request)
        )
    session.rollback()


def test_a_single_level_request_is_unchanged(fresh_db) -> None:
    session = fresh_db()
    from app.database import session_scope

    with session_scope() as create:
        request = student_service.create_student_request(
            create, student_request_payload(-7002, education_level="Grade 9-10")
        )
        assert request.education_level == "Grade 9-10"
    session.rollback()


def test_an_empty_level_list_is_rejected(fresh_db) -> None:
    session = fresh_db()
    from app.database import session_scope

    with session_scope() as create:
        with pytest.raises(student_service.StudentRequestValidationError):
            student_service.create_student_request(
                create, student_request_payload(-7003, education_level=[])
            )
    session.rollback()


def test_the_column_is_wide_enough_for_several_grades() -> None:
    from app.models import StudentRequest

    column = StudentRequest.__table__.columns["education_level"]
    # even the impossible "selected everything" case must not be truncated
    assert len(", ".join(GRADE_LEVELS)) <= column.type.length


# ---------------------------------------------------------------------------
# matching
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("requested", "taught", "expected"),
    [
        ("9th Grader", ["Grade 9-10"], True),
        ("Grade 7-8", ["7th Grader"], True),
        ("4th Grader", ["Grade 4-6"], True),
        ("Grade 7-8, Grade 9-10", ["Grade 9-10"], True),
        ("Grade 7-8, 9th Grader", ["7th Grader"], True),
        ("Grade 7-8, Grade 9-10", ["Grade 11-12"], False),
        ("KG", ["KG"], True),
        ("KG", ["Grade 1-3"], False),
        ("University", ["Postgraduate"], True),
        ("University", ["Grade 9-10"], False),
        ("All Levels", ["Grade 3-4"], True),
        ("Grade 9-10", ["All Levels"], True),
        ("Bogus Label", ["Bogus Label"], True),
        ("Grade 9-10", [], False),
        ("", ["Grade 9-10"], False),
        (None, ["Grade 9-10"], False),
    ],
)
def test_levels_overlap(requested, taught, expected) -> None:
    assert search_service.levels_overlap(requested, taught) is expected


def test_single_grade_requests_still_match_as_before(fresh_db) -> None:
    """A stored single level scores the level points, as it did pre-change."""
    from app.database import session_scope
    from app.enums import TutorStatus
    from tests.factories import create_tutor

    session = fresh_db()
    create_tutor(
        session,
        -7100,
        status=TutorStatus.VERIFIED,
        subjects=["Mathematics"],
        levels=["Grade 9-10"],
        languages=["English"],
        teaching_mode="ONLINE",
        country="Ethiopia",
        etb_rate=200.0,
    )
    with session_scope() as create:
        request = student_service.create_student_request(
            create,
            student_request_payload(
                -7101,
                education_level="Grade 9-10",
                subject="Mathematics",
                preferred_language="English",
                mode="ONLINE",
                budget=500,
            ),
        )
        matches = search_service.match_tutors(create, request, limit=5)

    assert matches, "a matching level must still produce a result"
    assert "level" in matches[0].reasons
    assert matches[0].score >= search_service.SCORE_LEVEL
    session.rollback()


def test_a_multi_level_request_matches_the_right_tutor(fresh_db) -> None:
    from app.database import session_scope
    from app.enums import TutorStatus
    from tests.factories import create_tutor

    session = fresh_db()
    create_tutor(
        session,
        -7200,
        status=TutorStatus.VERIFIED,
        subjects=["Mathematics"],
        levels=["Grade 9-10"],
        languages=["English"],
        teaching_mode="ONLINE",
        country="Ethiopia",
        etb_rate=200.0,
    )
    create_tutor(
        session,
        -7201,
        status=TutorStatus.VERIFIED,
        subjects=["Mathematics"],
        levels=["Grade 11-12"],
        languages=["English"],
        teaching_mode="ONLINE",
        country="Ethiopia",
        etb_rate=200.0,
    )
    with session_scope() as create:
        request = student_service.create_student_request(
            create,
            student_request_payload(
                -7202,
                education_level=["Grade 7-8", "9th Grader"],
                subject="Mathematics",
                preferred_language="English",
                mode="ONLINE",
                budget=500,
            ),
        )
        matches = search_service.match_tutors(create, request, limit=5)

    # Both tutors match on subject, so both are returned — level is a score,
    # not a filter. The tutor who teaches the requested grade must rank first
    # and be the only one credited with a level match.
    assert len(matches) == 2, [item.public_tutor_id for item in matches]
    assert "level" in matches[0].reasons
    assert "level" not in matches[1].reasons
    assert matches[0].score > matches[1].score
    session.rollback()
