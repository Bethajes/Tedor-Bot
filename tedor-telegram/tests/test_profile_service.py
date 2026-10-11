"""Structured tutor profile: creation, validation and completeness.

Feature: tutor-onboarding-and-matching, Ethiopian tutor onboarding
Validates: Requirements 23 (validation), 26 (enrichment), 27 (completion),
Requirement 2 (no pricing for Ethiopian tutors), Requirement 7 (the 50-member
rule is separate), Requirement 8 (field storage), Requirement 14 (score
normalisation inputs)
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings as hypothesis_settings
from hypothesis import strategies as st
from sqlalchemy.orm import Session

from app.enums import (
    ACADEMIC_DOCUMENT_TYPES,
    DocumentType,
    EducationLevel,
    MemberReferralStatus,
)
from app.models import Tutor
from app.services import location_service, tutor_profile_service as P
from app.services.tutor_service import TutorValidationError
from tests.factories import tutor_payload

PROFILE = hypothesis_settings(max_examples=200, deadline=None)

ACADEMIC_SET = {str(item) for item in ACADEMIC_DOCUMENT_TYPES}


def ethiopian_payload(telegram_id: int = -5001, **overrides):
    """A minimal valid payload: exactly what the Ethiopian flow collects."""
    payload = {
        "telegram_user_id": telegram_id,
        "full_name": "Abebe Bekele",
        "phone": "+251911234567",
        "gender": "Male",
        "age": 27,
        "current_address": "Bole, Addis Ababa",
        "locations": ["Ayat Tsebel"],
        "grades": ["Grade 4", "Grade 5"],
        "subjects": ["Mathematics"],
        "english_proficiency": 9,
        "university": "AASTU",
        "department": "Mathematics",
        "education_level": "UNIVERSITY_GRADUATE",
        "university_year": 2019,
        "cgpa": 3.75,
        "entrance_exam_score": 612,
        "entrance_exam_year": 2016,
        "entrance_exam_type": "EHE",
        "entrance_exam_max_score": 840,
        "teaching_experience_years": 3,
        "teaching_experience_description": "Taught Grade 4-6 mathematics in Ayat Tsebel.",
        "languages": ["Amharic", "English"],
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def tutor(db: Session) -> Tutor:
    session = db
    return P.create_ethiopian_tutor(session, ethiopian_payload())


# ---------------------------------------------------------------------------
# creation
# ---------------------------------------------------------------------------


def test_creation_stores_the_structured_profile(tutor: Tutor) -> None:
    assert tutor.public_tutor_id.startswith("TDR-")
    assert tutor.country == "Ethiopia"
    assert tutor.gender == "Male"
    assert tutor.age == 27
    assert tutor.current_address == "Bole, Addis Ababa"
    assert tutor.grade_list == ["Grade 4", "Grade 5"]
    assert tutor.subject_list == ["Mathematics"]
    assert tutor.location_list == ["Ayat Tsebel"]
    assert tutor.english_proficiency == 9
    assert tutor.university == "AASTU"
    assert tutor.department == "Mathematics"
    assert tutor.education_level == "UNIVERSITY_GRADUATE"
    assert tutor.cgpa == 3.75
    assert tutor.entrance_exam_score == 612
    assert tutor.entrance_exam_max_score == 840
    assert tutor.teaching_experience_years == 3
    assert tutor.status == "PENDING"


def test_a_new_tutor_starts_unverified(tutor: Tutor) -> None:
    assert tutor.status == "PENDING"
    assert tutor.verified_at is None
    assert tutor.verified_by is None


def test_no_price_is_ever_invented(tutor: Tutor) -> None:
    """Pricing belongs to the job side, so both rate columns stay NULL."""
    assert tutor.etb_rate is None
    assert tutor.usd_rate is None


def test_an_unasked_email_is_empty_not_invented(tutor: Tutor) -> None:
    """The new flow never asks for one; an empty value is the honest answer."""
    assert tutor.email == ""


def test_display_name_falls_back_to_the_full_name(tutor: Tutor) -> None:
    """The flow asks for one name, so it is not asked twice."""
    assert tutor.display_name == tutor.name == "Abebe Bekele"


def test_city_is_derived_from_the_named_areas(db: Session) -> None:
    tutor = P.create_ethiopian_tutor(db, ethiopian_payload(locations=["Kazanchis"]))
    assert tutor.city == "Addis Ababa"


def test_an_out_of_addis_area_derives_its_own_city(db: Session) -> None:
    tutor = P.create_ethiopian_tutor(db, ethiopian_payload(locations=["Adama"]))
    assert tutor.city == ""


def test_the_full_name_reuses_the_existing_name_column(tutor: Tutor) -> None:
    """No second column for the same concept."""
    assert not hasattr(tutor, "full_name")


def test_creation_is_idempotent_in_tdr_ids(db: Session) -> None:
    first = P.create_ethiopian_tutor(db, ethiopian_payload(-5001))
    second = P.create_ethiopian_tutor(db, ethiopian_payload(-5002))
    assert first.public_tutor_id != second.public_tutor_id


# ---------------------------------------------------------------------------
# required collections (section 23)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["locations", "grades", "subjects"])
def test_a_required_collection_cannot_be_empty(db: Session, field: str) -> None:
    with pytest.raises(P.ProfileValidationError):
        P.create_ethiopian_tutor(db, ethiopian_payload(**{field: []}))


def test_an_online_tutor_needs_no_location(db: Session) -> None:
    """Locations are required for in-person teaching only."""
    tutor = P.create_ethiopian_tutor(
        db, ethiopian_payload(locations=[], teaching_mode="ONLINE")
    )
    assert tutor.tutoring_locations == []
    assert tutor.teaching_mode == "ONLINE"


def test_teaching_mode_is_derived_from_the_areas(db: Session) -> None:
    assert (
        P.create_ethiopian_tutor(db, ethiopian_payload(locations=["Bole"])).teaching_mode
        == "IN_PERSON"
    )


def test_an_explicit_teaching_mode_wins(db: Session) -> None:
    tutor = P.create_ethiopian_tutor(
        db, ethiopian_payload(locations=["Bole"], teaching_mode="BOTH")
    )
    assert tutor.teaching_mode == "BOTH"


# ---------------------------------------------------------------------------
# numeric validation (section 23)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("age", [0, 5, 17, -3, 130, 200])
def test_age_outside_the_adult_range_is_rejected(age: int) -> None:
    with pytest.raises(P.ProfileValidationError):
        P.validate_age(age)


@pytest.mark.parametrize("age", [18, 19, 25, 60, 99])
def test_valid_ages_are_accepted(age: int) -> None:
    assert P.validate_age(age) == age


def test_a_non_numeric_age_is_rejected() -> None:
    with pytest.raises(P.ProfileValidationError):
        P.validate_age("twenty seven")


def test_a_fractional_age_is_rejected() -> None:
    with pytest.raises(P.ProfileValidationError):
        P.validate_age(27.5)


def test_a_missing_age_is_not_invented() -> None:
    assert P.validate_age(None) is None
    assert P.validate_age("") is None


@pytest.mark.parametrize("level", [1, 2, 5, 9, 10])
def test_english_levels_one_to_ten_are_accepted(level: int) -> None:
    assert P.validate_english_proficiency(level) == level


@pytest.mark.parametrize("level", [0, -1, 11, 25, 100])
def test_english_levels_outside_one_to_ten_are_rejected(level: int) -> None:
    with pytest.raises(P.ProfileValidationError):
        P.validate_english_proficiency(level)


def test_english_proficiency_is_an_integer_not_text() -> None:
    with pytest.raises(P.ProfileValidationError):
        P.validate_english_proficiency("very good")
    with pytest.raises(P.ProfileValidationError):
        P.validate_english_proficiency(7.5)


def test_an_unrated_english_level_is_not_invented() -> None:
    assert P.validate_english_proficiency(None) is None


@pytest.mark.parametrize("cgpa", [0, 1.5, 3.75, 4.0])
def test_valid_cgpas_are_accepted(cgpa: float) -> None:
    assert P.validate_cgpa(cgpa) == cgpa


@pytest.mark.parametrize("cgpa", [-0.1, 4.1, 5.0, 100])
def test_a_cgpa_outside_the_grading_system_is_rejected(cgpa: float) -> None:
    with pytest.raises(P.ProfileValidationError):
        P.validate_cgpa(cgpa)


def test_a_non_numeric_cgpa_is_rejected() -> None:
    with pytest.raises(P.ProfileValidationError):
        P.validate_cgpa("A-")


def test_entrance_score_must_be_numeric() -> None:
    with pytest.raises(P.ProfileValidationError):
        P.validate_entrance_exam_score("distinction")


def test_a_negative_entrance_score_is_rejected() -> None:
    with pytest.raises(P.ProfileValidationError):
        P.validate_entrance_exam_score(-10)


def test_an_entrance_score_above_its_own_maximum_is_rejected() -> None:
    with pytest.raises(P.ProfileValidationError):
        P.validate_entrance_exam_score(900, max_score=840)


def test_the_maximum_must_be_positive() -> None:
    with pytest.raises(P.ProfileValidationError):
        P.validate_entrance_exam_max_score(0)
    with pytest.raises(P.ProfileValidationError):
        P.validate_entrance_exam_max_score(-5)


def test_scores_from_different_scales_can_both_be_stored(db: Session) -> None:
    """Both keep their own maximum, so normalisation stays possible."""
    a = P.create_ethiopian_tutor(
        db, ethiopian_payload(-5101, entrance_exam_score=612, entrance_exam_max_score=840)
    )
    b = P.create_ethiopian_tutor(
        db, ethiopian_payload(-5102, entrance_exam_score=72, entrance_exam_max_score=100)
    )
    assert (a.entrance_exam_score, a.entrance_exam_max_score) == (612, 840)
    assert (b.entrance_exam_score, b.entrance_exam_max_score) == (72, 100)


@given(st.integers(min_value=0, max_value=840))
@PROFILE
def test_entrance_scores_within_bounds_are_accepted(score: int) -> None:
    assert P.validate_entrance_exam_score(score, max_score=840) == float(score)


@given(st.one_of(st.none(), st.integers(min_value=1, max_value=10)))
@PROFILE
def test_any_english_level_in_range_is_accepted(level) -> None:
    assert P.validate_english_proficiency(level) == level


# ---------------------------------------------------------------------------
# other validators (section 23)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("phone", ["+251911234567", "0911234567", "251911234567"])
def test_valid_phone_numbers_are_accepted(phone: str) -> None:
    assert P.validate_phone(phone) == phone


@pytest.mark.parametrize("phone", ["abc", "123", "+", "", "phone: 0911"])
def test_invalid_phone_numbers_are_rejected(phone: str) -> None:
    with pytest.raises(P.ProfileValidationError):
        P.validate_phone(phone)


def test_unknown_education_levels_are_rejected() -> None:
    assert P.validate_education_level("university_graduate") == str(
        EducationLevel.UNIVERSITY_GRADUATE
    )
    with pytest.raises(P.ProfileValidationError):
        P.validate_education_level("PhD candidate")


def test_unknown_exam_types_are_rejected() -> None:
    assert P.validate_entrance_exam_type("EHE") == "EHE"
    with pytest.raises(P.ProfileValidationError):
        P.validate_entrance_exam_type("JAMB")


# ---------------------------------------------------------------------------
# document validation (section 23)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["cv.pdf", "scan.jpg", "photo.PNG", "doc.webp"])
def test_pdf_and_image_uploads_are_accepted(name: str) -> None:
    assert P.validate_document_file(name) is not None


@pytest.mark.parametrize(
    "name", ["malware.exe", "sheet.xlsx", "archive.zip", "script.sh", "notes.txt"]
)
def test_other_file_types_are_rejected(name: str) -> None:
    with pytest.raises(P.ProfileValidationError):
        P.validate_document_file(name)


def test_a_disallowed_mime_type_is_rejected() -> None:
    with pytest.raises(P.ProfileValidationError):
        P.validate_document_file(None, "application/x-msdownload")
    assert P.validate_document_file(None, "application/pdf") is None


def test_an_unknown_document_type_is_rejected(tutor: Tutor, db: Session) -> None:
    with pytest.raises(P.ProfileValidationError):
        P.record_document(db, tutor, "SELFIE", file_id="f", file_name="a.jpg")


def test_uploading_the_same_document_type_replaces_the_reference(
    tutor: Tutor, db: Session
) -> None:
    P.record_document(db, tutor, "UNIVERSITY_TRANSCRIPT", "old", "u1", 1, file_name="a.pdf")
    P.record_document(db, tutor, "UNIVERSITY_TRANSCRIPT", "new", "u2", 2, file_name="b.pdf")
    rows = [
        d for d in tutor.documents if d.document_type == "UNIVERSITY_TRANSCRIPT"
    ]
    assert len(rows) == 1
    assert rows[0].telegram_file_id == "new"


def test_documents_keep_the_telegram_references(tutor: Tutor, db: Session) -> None:
    document = P.record_document(
        db, tutor, "UNIVERSITY_ENTRANCE_RESULT", "file-abc", "uniq-abc", 55,
        file_name="result.pdf",
    )
    assert document.telegram_file_id == "file-abc"
    assert document.telegram_file_unique_id == "uniq-abc"
    assert document.telegram_message_id == 55


def test_all_academic_document_types_are_accepted(tutor: Tutor, db: Session) -> None:
    for index, kind in enumerate(sorted(ACADEMIC_SET)):
        P.record_document(db, tutor, kind, f"file-{index}", file_name="doc.pdf")
    stored = {d.document_type for d in tutor.documents}
    assert ACADEMIC_SET <= stored


# ---------------------------------------------------------------------------
# English voice (section 3 D)
# ---------------------------------------------------------------------------


def test_the_voice_introduction_stores_file_and_message_ids(
    tutor: Tutor, db: Session
) -> None:
    P.record_english_voice(db, tutor, "voice-file-id", 4242)
    assert tutor.english_voice_file_id == "voice-file-id"
    assert tutor.english_voice_message_id == 4242
    assert tutor.has_english_voice is True


def test_the_voice_introduction_is_also_an_archived_document(
    tutor: Tutor, db: Session
) -> None:
    P.record_english_voice(db, tutor, "voice-file-id", 4242)
    kinds = {d.document_type for d in tutor.documents}
    assert str(DocumentType.ENGLISH_VOICE) in kinds


def test_the_voice_introduction_is_optional(tutor: Tutor) -> None:
    assert tutor.has_english_voice is False
    assert tutor.english_proficiency == 9, "the numeric rating stands on its own"


def test_rerecording_the_voice_replaces_the_reference(tutor: Tutor, db: Session) -> None:
    P.record_english_voice(db, tutor, "voice-1", 1)
    P.record_english_voice(db, tutor, "voice-2", 2)
    rows = [d for d in tutor.documents if d.document_type == str(DocumentType.ENGLISH_VOICE)]
    assert len(rows) == 1
    assert tutor.english_voice_file_id == "voice-2"


def test_an_empty_voice_file_is_rejected(tutor: Tutor, db: Session) -> None:
    with pytest.raises(P.ProfileValidationError):
        P.record_english_voice(db, tutor, "", 1)


@pytest.mark.parametrize("seconds", [1, 20, 29, 61, 120, 300])
def test_out_of_window_durations_are_flagged_but_not_refused(seconds: int) -> None:
    """The clip is advisory; losing real evidence would be worse."""
    assert P.english_voice_duration_ok(seconds) is False


def test_an_unknown_duration_is_not_judged() -> None:
    """Telegram omits the duration for some clips; that is not a short one."""
    assert P.english_voice_duration_ok(None) is True


@pytest.mark.parametrize("seconds", [30, 45, 60])
def test_in_window_durations_are_accepted(seconds: int) -> None:
    assert P.english_voice_duration_ok(seconds) is True


# ---------------------------------------------------------------------------
# conditional academic documents (section 4)
# ---------------------------------------------------------------------------


def test_undergraduates_are_not_asked_for_an_msc_document() -> None:
    asked = P.academic_document_types_for(EducationLevel.UNIVERSITY_STUDENT)
    assert str(DocumentType.MSC_DEGREE) not in asked
    assert str(DocumentType.CURRENT_UNIVERSITY_DOCUMENT) in asked


def test_graduates_are_asked_for_their_degree() -> None:
    asked = P.academic_document_types_for(EducationLevel.UNIVERSITY_GRADUATE)
    assert str(DocumentType.BACHELORS_DEGREE) in asked


def test_master_graduates_are_asked_for_their_degree() -> None:
    asked = P.academic_document_types_for(EducationLevel.MASTERS_GRADUATE)
    assert str(DocumentType.MSC_DEGREE) in asked


def test_no_documentation_is_forced_before_a_level_is_known() -> None:
    asked = P.academic_document_types_for(None)
    assert str(DocumentType.MSC_DEGREE) not in asked
    assert asked


# ---------------------------------------------------------------------------
# the 50-member rule (section 7)
# ---------------------------------------------------------------------------


def test_the_member_requirement_starts_not_completed(tutor: Tutor) -> None:
    assert tutor.member_referral_status == str(MemberReferralStatus.NOT_COMPLETED)


@pytest.mark.parametrize(
    "status",
    [
        MemberReferralStatus.PENDING_CONFIRMATION,
        MemberReferralStatus.COMPLETED,
        MemberReferralStatus.VERIFIED,
    ],
)
def test_the_admin_can_move_the_member_requirement(
    tutor: Tutor, db: Session, status
) -> None:
    P.set_member_referral_status(db, tutor, status, verified_by=111)
    assert tutor.member_referral_status == str(status)


def test_an_unknown_member_status_is_rejected(tutor: Tutor, db: Session) -> None:
    with pytest.raises(P.ProfileValidationError):
        P.set_member_referral_status(db, tutor, "MAYBE")


def test_the_member_requirement_is_not_part_of_profile_completeness(
    tutor: Tutor, db: Session
) -> None:
    """Adding members is never evidence of tutoring quality."""
    before = P.profile_completion_percentage(tutor)
    for status in MemberReferralStatus:
        P.set_member_referral_status(db, tutor, status)
        assert P.profile_completion_percentage(tutor) == before


def test_completion_is_unaffected_by_the_member_status_source() -> None:
    """A stronger guard: the field is not read by the completeness code."""
    import inspect

    completeness = inspect.getsource(P._is_present)
    completeness += inspect.getsource(P._field_present)
    completeness += inspect.getsource(P.missing_profile_fields)
    completeness += inspect.getsource(P.profile_completion_percentage)
    assert "member_referral" not in completeness


# ---------------------------------------------------------------------------
# completeness (section 27)
# ---------------------------------------------------------------------------


def test_a_complete_profile_scores_high(tutor: Tutor, db: Session) -> None:
    P.record_document(db, tutor, "UNIVERSITY_TRANSCRIPT", "f", file_name="t.pdf")
    report = P.completion_report(tutor)
    assert report["percentage"] == 100
    assert report["complete"] is True
    assert report["missing"] == []


def test_completion_is_a_percentage_between_zero_and_a_hundred(
    tutor: Tutor, db: Session
) -> None:
    """Every tutor scores inside the range, however much was recorded."""
    from app.services import tutor_service

    # A legacy import: the 1,000+ tutors already on file have none of the new
    # structured fields.
    bare = tutor_service.create_tutor(db, tutor_payload(-5201))
    for percentage in (
        P.profile_completion_percentage(tutor),
        P.profile_completion_percentage(bare),
    ):
        assert 0 <= percentage <= 100
    # The legacy record has subjects, so it is not zero — but it is missing
    # every new field, so it scores far below the freshly onboarded tutor.
    assert 0 < P.profile_completion_percentage(bare) < 20
    assert P.profile_completion_percentage(bare) < P.profile_completion_percentage(tutor)


def test_an_imported_tutor_scores_far_below_an_onboarded_one(db: Session) -> None:
    """The 1,000+ existing tutors predate every new field."""
    from app.services import tutor_service

    legacy = tutor_service.create_tutor(db, tutor_payload(-5301))
    fresh = P.create_ethiopian_tutor(db, ethiopian_payload(-5302))
    assert P.profile_completion_percentage(legacy) < 20
    assert P.profile_completion_percentage(fresh) > P.profile_completion_percentage(legacy)
    assert "locations" in P.missing_profile_fields(legacy)


def test_missing_fields_are_reported_in_the_enrichment_priority_order(
    tutor: Tutor, db: Session
) -> None:
    """Section 26 order: locations, grades, subjects, English, exam, ..."""
    order = [field.key for field in P.PROFILE_FIELDS]
    assert order[:8] == [
        "locations",
        "grades",
        "subjects",
        "english_proficiency",
        "entrance_exam_score",
        "university",
        "experience",
        "documents",
    ], "the enrichment flow must ask in this order"

    missing = P.missing_profile_fields(tutor)
    assert missing == sorted(missing, key=order.index), "not in priority order"
    # Everything but the documents was supplied above.
    assert missing == ["documents"]

    # A legacy import reports its gaps in that order too. The legacy payload
    # does carry subjects and levels, so those are absent from the report.
    from app.services import tutor_service

    legacy = tutor_service.create_tutor(db, tutor_payload(-5202))
    legacy_missing = P.missing_profile_fields(legacy)
    assert legacy_missing == sorted(legacy_missing, key=order.index)
    assert legacy_missing[0] == "locations"
    assert "english_proficiency" in legacy_missing
    assert legacy_missing.index("locations") < legacy_missing.index("english_proficiency")
    assert legacy_missing.index("english_proficiency") < legacy_missing.index("documents")


def test_missing_labels_are_human_readable(tutor: Tutor) -> None:
    labels = P.missing_profile_labels(tutor)
    assert labels
    assert all(isinstance(label, str) and label.strip() for label in labels)
    assert not any("<" in label for label in labels), "labels are not markup"


def test_the_voice_recording_is_never_counted_as_completeness(
    tutor: Tutor, db: Session
) -> None:
    """Otherwise sending a voice clip would look like better teaching."""
    before = P.profile_completion_percentage(tutor)
    P.record_english_voice(db, tutor, "voice", 1)
    assert P.profile_completion_percentage(tutor) == before


def test_a_tutor_who_skipped_the_voice_can_still_be_complete(
    tutor: Tutor, db: Session
) -> None:
    P.record_document(db, tutor, "UNIVERSITY_TRANSCRIPT", "f", file_name="t.pdf")
    assert P.profile_completion_percentage(tutor) == 100


def test_completion_is_deterministic(tutor: Tutor) -> None:
    scores = {P.profile_completion_percentage(tutor) for _ in range(5)}
    assert len(scores) == 1


@given(
    age=st.integers(min_value=18, max_value=80),
    seed=st.integers(min_value=1, max_value=10**6),
)
@PROFILE
def test_completion_never_depends_on_age_alone(fresh_db, age: int, seed: int) -> None:
    """Age is one small weighted field, not a quality signal.

    ``seed`` varies the Telegram ID per example, as the other property tests
    do, so generated runs never collide on the unique constraint.
    """
    session = fresh_db()
    first = P.create_ethiopian_tutor(session, ethiopian_payload(-seed - 1, age=age))
    second = P.create_ethiopian_tutor(session, ethiopian_payload(-seed - 10_000_001, age=age))
    assert P.profile_completion_percentage(first) == P.profile_completion_percentage(second)


# ---------------------------------------------------------------------------
# enrichment (section 26)
# ---------------------------------------------------------------------------


def test_enrichment_sets_only_what_was_missing(db: Session) -> None:
    legacy = tutor_service_create(db)
    assert "locations" in P.missing_profile_fields(legacy)
    P.set_locations(db, legacy, ["Bole", "Gerji"])
    P.set_grades(db, legacy, ["Grade 7", "Grade 8"])
    P.set_subjects(db, legacy, ["Physics"])
    P.set_english_proficiency(db, legacy, 8)
    assert "locations" not in P.missing_profile_fields(legacy)
    assert legacy.normalized_location_list == ["bole", "gerji"]
    assert P.profile_completion_percentage(legacy) > 0


def tutor_service_create(db: Session) -> Tutor:
    from app.services import tutor_service

    return tutor_service.create_tutor(db, tutor_payload(-5501))


def test_enrichment_keeps_legacy_data(db: Session) -> None:
    legacy = tutor_service_create(db)
    original_rate = legacy.etb_rate
    P.set_locations(db, legacy, ["Bole"])
    assert legacy.etb_rate == original_rate
    assert legacy.subject_list == ["Mathematics"]


def test_enrichment_validates_what_it_stores(db: Session) -> None:
    legacy = tutor_service_create(db)
    with pytest.raises(P.ProfileValidationError):
        P.set_english_proficiency(db, legacy, 99)
    with pytest.raises(P.ProfileValidationError):
        P.set_subjects(db, legacy, [])


def test_update_profile_fields_validates_scalars(db: Session) -> None:
    legacy = tutor_service_create(db)
    P.update_profile_fields(
        db,
        legacy,
        {
            "age": 30,
            "cgpa": 3.2,
            "university": "AAU",
            "education_level": "UNIVERSITY_STUDENT",
            "entrance_exam_score": 500,
            "entrance_exam_max_score": 840,
        },
    )
    assert legacy.age == 30
    assert legacy.cgpa == 3.2
    assert legacy.university == "AAU"
    assert legacy.entrance_exam_score == 500


def test_update_profile_fields_rejects_invalid_scalars(db: Session) -> None:
    legacy = tutor_service_create(db)
    with pytest.raises(P.ProfileValidationError):
        P.update_profile_fields(db, legacy, {"age": 5})


def test_update_profile_fields_ignores_unknown_keys(db: Session) -> None:
    legacy = tutor_service_create(db)
    P.update_profile_fields(db, legacy, {"etb_rate": 9999, "status": "VERIFIED"})
    assert legacy.etb_rate is not None


def test_experience_rows_are_stored_for_relevance_scoring(
    tutor: Tutor, db: Session
) -> None:
    P.add_experience(
        db,
        tutor,
        {
            "years": 2,
            "description": "Private tutoring for Grade 4",
            "subjects": ["Mathematics"],
            "grades": ["Grade 4"],
            "institutions": ["Self-employed"],
        },
    )
    assert len(tutor.experiences) == 1
    assert tutor.experiences[0].subjects == "Mathematics"


def test_several_experience_rows_accumulate_the_total(tutor: Tutor, db: Session) -> None:
    P.add_experience(db, tutor, {"years": 2})
    P.add_experience(db, tutor, {"years": 3})
    assert tutor.teaching_experience_years == 8


# ---------------------------------------------------------------------------
# privacy and separation from the legacy flow (sections 24, 30)
# ---------------------------------------------------------------------------


def test_profile_errors_are_catchable_as_the_legacy_validation_error() -> None:
    """Existing handler ``except`` clauses keep working unchanged."""
    assert issubclass(P.ProfileValidationError, TutorValidationError)
    with pytest.raises(TutorValidationError):
        P.validate_age(10)


def test_the_legacy_flow_is_unaffected() -> None:
    """The 26-step path still requires everything it always did."""
    from app.services import tutor_service

    assert "email" in tutor_service.REQUIRED_FIELDS
    assert "country" in tutor_service.REQUIRED_FIELDS


def test_legacy_and_ethiopian_tutors_coexist_in_one_table(db: Session) -> None:
    from app.services import tutor_service

    legacy = tutor_service.create_tutor(db, tutor_payload(-5601))
    fresh = P.create_ethiopian_tutor(db, ethiopian_payload(-5602))
    assert legacy.tutoring_locations == []
    assert fresh.tutoring_locations != []


def test_creating_an_ethiopian_tutor_does_not_invent_a_phone(db: Session) -> None:
    with pytest.raises(P.ProfileValidationError):
        P.create_ethiopian_tutor(db, ethiopian_payload(phone=""))


# ---------------------------------------------------------------------------
# multi-location storage (section 8)
# ---------------------------------------------------------------------------


def test_multiple_locations_are_stored_separately(db: Session) -> None:
    tutor = P.create_ethiopian_tutor(
        db,
        ethiopian_payload(
            locations=["Ayat Tsebel", "Bole", "Kazanchis", "Saris", "CMC", "Gerji"]
        ),
    )
    assert tutor.location_list == [
        "Ayat Tsebel",
        "Bole",
        "Kazanchis",
        "Saris",
        "CMC",
        "Gerji",
    ]
    assert len(tutor.tutoring_locations) == 6


def test_each_location_is_stored_with_its_canonical_key(db: Session) -> None:
    tutor = P.create_ethiopian_tutor(
        db, ethiopian_payload(locations=["Ayat Tsebel", "Bole, Addis Ababa", "ቦሌ"])
    )
    keys = tutor.normalized_location_list
    assert "ayat tsebel" in keys
    # "Bole, Addis Ababa" and "ቦሌ" are the same area, so one row is kept.
    assert keys.count("bole") == 1
    assert tutor.location_list[keys.index("bole")] == "Bole, Addis Ababa"


def test_duplicate_spellings_collapse_to_one_row(db: Session) -> None:
    tutor = P.create_ethiopian_tutor(
        db, ethiopian_payload(locations=["Ayat Tsebel", "ayat_tsebel"])
    )
    assert len(tutor.tutoring_locations) == 1


def test_duplicate_spellings_are_case_and_punctuation_insensitive(db: Session) -> None:
    tutor = P.create_ethiopian_tutor(
        db, ethiopian_payload(locations=["Bole", "  bole ", "BOLE", "Bole."])
    )
    assert len(tutor.tutoring_locations) == 1


def test_stored_locations_are_searchable_by_canonical_key(db: Session) -> None:
    tutor = P.create_ethiopian_tutor(db, ethiopian_payload(locations=["Ayat_Tsebel"]))
    assert location_service.normalize_location(tutor.location_list[0]) == "ayat tsebel"


def test_replacing_locations_does_not_duplicate(db: Session) -> None:
    tutor = P.create_ethiopian_tutor(db, ethiopian_payload(locations=["Bole", "Gerji"]))
    P.set_locations(db, tutor, ["CMC"])
    assert tutor.location_list == ["CMC"]
    assert len(tutor.tutoring_locations) == 1


def test_resaving_an_unchanged_collection_does_not_violate_uniqueness(
    db: Session,
) -> None:
    """Regression: re-submitting the same subjects must not break.

    A blanket delete-then-insert flushes the insert before the delete and trips
    ``UNIQUE(tutor_id, subject)``, so a tutor who re-confirms what they already
    told us would have been rejected.
    """
    tutor = P.create_ethiopian_tutor(db, ethiopian_payload())
    P.set_subjects(db, tutor, ["Mathematics"])
    assert tutor.subject_list == ["Mathematics"]

    P.set_grades(db, tutor, ["Grade 4"])
    assert tutor.grade_list == ["Grade 4"]

    P.set_locations(db, tutor, ["Ayat Tsebel"])
    assert tutor.location_list == ["Ayat Tsebel"]


def test_adding_and_dropping_a_value_still_works(db: Session) -> None:
    tutor = P.create_ethiopian_tutor(db, ethiopian_payload(subjects=["Mathematics"]))
    P.set_subjects(db, tutor, ["Mathematics", "Physics"])
    assert sorted(tutor.subject_list) == ["Mathematics", "Physics"]
    P.set_subjects(db, tutor, ["Physics"])
    assert tutor.subject_list == ["Physics"]


def test_grades_are_stored_individually(db: Session) -> None:
    tutor = P.create_ethiopian_tutor(
        db, ethiopian_payload(grades=["Elementary", *[f"Grade {n}" for n in range(1, 13)], "University"])
    )
    assert len(tutor.grades) == 14


# ---------------------------------------------------------------------------
# clean_values
# ---------------------------------------------------------------------------


def test_clean_values_deduplicates_and_trims() -> None:
    assert P.clean_values([" Bole ", "Bole", "", None, "Gerji"]) == ["Bole", "Gerji"]


def test_clean_values_accepts_a_comma_string() -> None:
    assert P.clean_values("Mathematics, Physics , Mathematics") == [
        "Mathematics",
        "Physics",
    ]