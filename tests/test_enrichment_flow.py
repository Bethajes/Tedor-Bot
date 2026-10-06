"""``/complete_profile`` — enrichment that asks only what is missing.

Spec section 26: existing tutors open the bot and fill in only the outstanding
information, prioritised as locations, grades, subjects, English, entrance exam,
university, experience, documents. Nothing already on file is re-requested and
no second tutor is ever created.
"""

from __future__ import annotations

import pytest
from telegram.ext import ConversationHandler

from app.bot.handlers import tutor_onboarding as eth
from app.bot.states import EthiopianTutorStates
from app.database import session_scope
from app.services import tutor_profile_service as P
from app.services import tutor_service
from tests.factories import tutor_payload
from tests.test_ethiopian_onboarding import FakeContext, drive

def _plan(tutor) -> list[str]:
    """The planned fields, in the order the plan will ask for them.

    Built from the plan itself, not from ``STEPS`` — ``STEPS`` is the
    canonical declaration order, so filtering it would hide any reordering.
    """
    return [eth.STEPS_BY_STATE[state].field for state in eth.enrichment_steps(tutor)]


def _legacy_tutor(session, telegram_id: int = 4242, **overrides):
    """A pre-existing imported tutor: no new structured fields at all."""
    payload = tutor_payload(telegram_id, **overrides)
    return tutor_service.create_tutor(session, payload)


#: A complete enrichment run for a tutor who only lacks the structured fields.
#: ``subjects`` and the university questions are absent on purpose: the legacy
#: tutor already has them, so the plan must not ask.
_ENRICH_ANSWERS = [
    ("callback", "eth:locations:toggle:0"),      # Ayat Tsebel
    ("callback", "eth:locations:done"),
    ("callback", "eth:grades:toggle:4"),         # Grade 4
    ("callback", "eth:grades:done"),
    ("callback", "eth:english_proficiency:pick:8"),  # 9 / Excellent
    ("callback", "eth:education_level:pick:1"),  # University graduate
    ("callback", "eth:entrance_exam_type:pick:0"),  # EHE
    ("text", "840"),                             # max score
    ("text", "612"),                             # raw score
    ("text", "2016"),                            # exam year
    ("text", "3"),                               # years of experience
    ("text", "Taught Grade 4 mathematics in Ayat Tsebel."),
    ("callback", "eth:continue"),                # skip documents
]


def _stored_tutor(session, telegram_id: int = 4242, **overrides):
    """A tutor with the personal details on file but no structured answers."""
    tutor = _legacy_tutor(session, telegram_id, **overrides)
    tutor.name = "Abebe Bekele"
    tutor.gender = "Male"
    tutor.phone = "+251911234567"
    P.update_profile_fields(
        session, tutor, {"age": 30, "current_address": "Bole"}
    )
    session.commit()
    return tutor


async def _run_enrichment(conversation, context, *, submit=True):
    """Answer a full enrichment run for the tutor already in the database.

    The tutor must already exist: this helper only drives the conversation, so
    that a test can set it up however it needs to.
    """
    state = await drive(
        conversation,
        context,
        [("command", "complete_profile")] + _ENRICH_ANSWERS,
        start=False,
    )
    if submit and state == int(eth.EthiopianTutorStates.SUMMARY):
        state = await drive(
            conversation, context, [("callback", "eth:submit")], state=state
        )
    return state


def _complete_tutor(session):
    """A tutor with nothing left to fill in."""
    tutor = _stored_tutor(session)
    P.set_locations(session, tutor, ["Ayat Tsebel"])
    P.set_grades(session, tutor, ["Grade 4"])
    P.set_subjects(session, tutor, ["Mathematics"])
    P.set_english_proficiency(session, tutor, 9)
    P.update_profile_fields(
        session,
        tutor,
        {
            "education_level": "UNIVERSITY_GRADUATE",
            "entrance_exam_type": "EHE",
            "entrance_exam_max_score": 840,
            "entrance_exam_score": 612,
            "entrance_exam_year": 2016,
            "teaching_experience_years": 3,
        },
    )
    P.record_document(
        session,
        tutor,
        "UNIVERSITY_TRANSCRIPT",
        file_name="transcript.pdf",
        mime_type="application/pdf",
    )
    session.commit()
    return tutor


# ---------------------------------------------------------------------------
# planning
# ---------------------------------------------------------------------------


def test_planning_follows_the_section_26_priority_order(api_env, fresh_db) -> None:
    session = fresh_db()
    tutor = _stored_tutor(session)
    tutor.subjects = []
    tutor.grades = []
    tutor.tutoring_locations = []
    session.flush()

    fields = _plan(tutor)

    assert fields == [
        "locations",
        "grades",
        "subjects",
        "english_proficiency",
        "education_level",
        "entrance_exam_type",
        "entrance_exam_max_score",
        "entrance_exam_score",
        "entrance_exam_year",
        "teaching_experience_years",
        "teaching_experience_description",
        "documents",
        "summary",
    ]


def test_planning_never_asks_for_what_is_already_stored(api_env, fresh_db) -> None:
    """§26: no re-asking, including answers that live in the legacy records."""
    session = fresh_db()
    tutor = _stored_tutor(session)
    P.set_locations(session, tutor, ["Ayat Tsebel"])
    P.set_english_proficiency(session, tutor, 9)
    P.update_profile_fields(
        session,
        tutor,
        {
            "education_level": "UNIVERSITY_GRADUATE",
            "entrance_exam_type": "EHE",
            "entrance_exam_max_score": 840,
            "entrance_exam_score": 612,
            "entrance_exam_year": 2016,
        },
    )
    session.commit()

    fields = _plan(tutor)

    for already in ("locations", "english_proficiency", "education_level", "university"):
        assert already not in fields
    # The legacy education record answers the university questions.
    assert tutor.education[0].institution == "Addis Ababa University"
    for already in ("university", "department", "university_year"):
        assert already not in fields
    assert "grades" in fields


def test_a_complete_tutor_plans_nothing_but_the_summary(api_env, fresh_db) -> None:
    session = fresh_db()
    tutor = _complete_tutor(session)
    profile_service_backfill = P.backfill_education_from_legacy(session, tutor)
    session.commit()

    assert eth.enrichment_steps(tutor) == [int(eth.EthiopianTutorStates.SUMMARY)]
    assert profile_service_backfill, "the legacy record should backfill the columns"
    assert P.profile_completion_percentage(tutor) == 100


def test_the_entrance_exam_is_asked_as_a_whole_chain(api_env, fresh_db) -> None:
    """A raw score with no scale is unusable, so the whole chain is asked."""
    session = fresh_db()
    tutor = _stored_tutor(session)
    P.update_profile_fields(session, tutor, {"entrance_exam_score": 612})
    session.commit()

    fields = _plan(tutor)
    chain = [
        "entrance_exam_type",
        "entrance_exam_max_score",
        "entrance_exam_score",
        "entrance_exam_year",
    ]
    for name in chain:
        assert name in fields
    positions = [fields.index(name) for name in chain]
    assert positions == sorted(positions), "the chain must be asked in order"
    assert fields.index("education_level") < positions[0]


def test_the_entrance_exam_is_not_re_asked_when_complete(api_env, fresh_db) -> None:
    session = fresh_db()
    tutor = _stored_tutor(session)
    P.update_profile_fields(
        session,
        tutor,
        {
            "education_level": "UNIVERSITY_GRADUATE",
            "entrance_exam_type": "EHE",
            "entrance_exam_max_score": 840,
            "entrance_exam_score": 612,
            "entrance_exam_year": 2016,
        },
    )
    session.commit()

    fields = _plan(tutor)
    assert "entrance_exam_score" not in fields
    assert "entrance_exam_max_score" not in fields
    assert "education_level" not in fields


def test_the_voice_intro_is_never_re_asked(api_env, fresh_db) -> None:
    """The recording is optional, so a tutor without one is never nagged."""
    session = fresh_db()
    tutor = _stored_tutor(session)
    fields = _plan(tutor)

    assert not [name for name in fields if "voice" in name]
    assert int(eth.EthiopianTutorStates.ENGLISH_VOICE) not in eth.enrichment_steps(tutor)


def test_a_plan_always_ends_at_the_summary(api_env, fresh_db) -> None:
    session = fresh_db()
    steps = eth.enrichment_steps(_stored_tutor(session))
    assert steps[-1] == int(eth.EthiopianTutorStates.SUMMARY)
    assert steps.count(int(eth.EthiopianTutorStates.SUMMARY)) == 1


@pytest.mark.asyncio
async def test_the_command_tells_a_new_user_to_apply_first(api_env, fresh_db) -> None:
    conversation = eth.build_conversation()
    context = FakeContext()

    state = await drive(
        conversation, context, [("command", "complete_profile")], start=False
    )

    assert state == ConversationHandler.END
    assert "Become a Tutor" in context.bot.sent[-1]


@pytest.mark.asyncio
async def test_the_command_reports_a_complete_profile(api_env, fresh_db) -> None:
    session = fresh_db()
    _complete_tutor(session)
    conversation = eth.build_conversation()
    context = FakeContext()

    state = await drive(
        conversation, context, [("command", "complete_profile")], start=False
    )

    assert state == ConversationHandler.END
    assert "already complete" in context.bot.sent[-1]


@pytest.mark.asyncio
async def test_the_command_announces_what_is_missing(api_env, fresh_db) -> None:
    session = fresh_db()
    _stored_tutor(session)
    conversation = eth.build_conversation()
    context = FakeContext()

    state = await drive(
        conversation, context, [("command", "complete_profile")], start=False
    )

    assert state == int(eth.EthiopianTutorStates.LOCATIONS)
    opening = "\n".join(context.bot.sent)
    assert "Profile completion" in opening
    assert "Grades" in opening
    assert "Subjects" not in opening, "the legacy tutor already teaches subjects"


@pytest.mark.asyncio
async def test_enrichment_starts_at_the_first_missing_step(api_env, fresh_db) -> None:
    """Grades are on file, so the run opens on locations, not on step one."""
    session = fresh_db()
    tutor = _stored_tutor(session)
    P.set_grades(session, tutor, ["Grade 4"])
    session.commit()

    conversation = eth.build_conversation()
    context = FakeContext()
    state = await drive(
        conversation, context, [("command", "complete_profile")], start=False
    )

    assert state == int(eth.EthiopianTutorStates.LOCATIONS)
    session.expire_all()
    assert tutor_service.get_tutor_by_telegram_id(session, 4242).grade_list == ["Grade 4"]


@pytest.mark.asyncio
async def test_enrichment_applies_the_answers_to_the_same_tutor(
    api_env, fresh_db
) -> None:
    """No second profile, and the stored values are the ones given."""
    session = fresh_db()
    tutor = _stored_tutor(session)
    original_tdr = tutor.public_tutor_id

    conversation = eth.build_conversation()
    context = FakeContext()
    state = await drive(
        conversation,
        context,
        [
            ("command", "complete_profile"),
            ("callback", "eth:locations:toggle:0"),
            ("callback", "eth:locations:done"),
        ],
        start=False,
    )
    assert state == int(eth.EthiopianTutorStates.GRADES)

    # Cancelling must not persist anything.
    await drive(conversation, context, [("callback", "eth:cancel")], state=state)
    session.expire_all()
    assert tutor_service.get_tutor_by_telegram_id(session, 4242).location_list == []

    # A full run updates the very same tutor.
    await _run_enrichment(conversation, context)

    session.expire_all()
    stored = tutor_service.get_tutor_by_telegram_id(session, 4242)
    assert stored.public_tutor_id == original_tdr, "enrichment must not create a tutor"
    assert stored.location_list == ["Ayat Tsebel"]
    assert session.query(tutor_service.Tutor).count() == 1


@pytest.mark.asyncio
async def test_enrichment_validates_against_the_stored_maximum(
    api_env, fresh_db
) -> None:
    """The same rule as the full application: 900 is invalid against 840."""
    session = fresh_db()
    _stored_tutor(session)

    conversation = eth.build_conversation()
    context = FakeContext()
    state = await drive(
        conversation,
        context,
        [("command", "complete_profile")]
        + _ENRICH_ANSWERS[: _ENRICH_ANSWERS.index(("text", "840")) + 1]
        + [("text", "900")],
        start=False,
    )

    assert state == int(eth.EthiopianTutorStates.ENTRANCE_SCORE)


@pytest.mark.asyncio
async def test_enrichment_reports_the_new_completion_percentage(
    api_env, fresh_db
) -> None:
    session = fresh_db()
    tutor = _stored_tutor(session)
    before = eth.tutor_profile_completion(tutor)

    conversation = eth.build_conversation()
    context = FakeContext()
    state = await _run_enrichment(conversation, context)

    assert state == ConversationHandler.END
    rendered = "\n".join(context.bot.sent)
    assert "Profile updated" in rendered

    session.expire_all()
    stored = tutor_service.get_tutor_by_telegram_id(session, 4242)
    after = eth.tutor_profile_completion(stored)
    assert after > before
    assert f"Profile completeness: <b>{after}%</b>" in rendered
    assert stored.location_list == ["Ayat Tsebel"]
    assert stored.grade_list == ["Grade 4"]
    assert stored.english_proficiency == 9
    assert stored.entrance_exam_score == 612
    assert stored.entrance_exam_max_score == 840
    assert stored.entrance_exam_type == "EHE"
    assert stored.education_level == "UNIVERSITY_GRADUATE"
    assert stored.teaching_experience_years == 3


@pytest.mark.asyncio
async def test_enrichment_does_not_disturb_untouched_data(api_env, fresh_db) -> None:
    session = fresh_db()
    tutor = _stored_tutor(session)
    tutor.bio = "Twenty years of classroom teaching."
    tutor.city = "Addis Ababa"
    tutor.public_tutor_id = original_tdr = tutor.public_tutor_id
    session.commit()

    conversation = eth.build_conversation()
    context = FakeContext()
    await _run_enrichment(conversation, context)

    session.expire_all()
    stored = tutor_service.get_tutor_by_telegram_id(session, 4242)
    assert stored.bio == "Twenty years of classroom teaching."
    assert stored.city == "Addis Ababa"
    assert stored.public_tutor_id == original_tdr
    assert stored.name == "Abebe Bekele"
    assert stored.gender == "Male"
    assert stored.age == 30
    assert stored.current_address == "Bole"
