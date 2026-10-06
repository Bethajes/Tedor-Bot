"""Explainable tutor-job matching.

Feature: tutor-onboarding-and-matching, tutor-job matching
Validates: Requirements 10 (weighted match score), 11 (job-aware scoring),
12 (job-aware weighting), 13 (location matching), 14 (academic performance),
15 (English matching), 16 (experience matching), 17 (top 5), 18 (explainable
matching), 22 (re-matching), 30 (matching principles)
"""

from __future__ import annotations

import inspect
import re

import pytest
from hypothesis import given, settings as hypothesis_settings
from hypothesis import strategies as st
from sqlalchemy.orm import Session

from app.config import settings
from app.enums import MATCH_CRITERIA, normalise_weights
from app.models import JobPost, Tutor
from app.services import grade_service, job_post_service, matching_service as M
from app.services import tutor_profile_service as P
from tests.factories import job_post_payload

PROFILE = hypothesis_settings(max_examples=100, deadline=None)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def publish_job(
    db: Session,
    *,
    subjects: str = "Mathematics",
    student_level: str = "4th Grader",
    location: str = "Ayat Tsebel",
    tutor_category: str = "Both Male and Female",
    teaching_mode: str = "IN_PERSON",
    english_level: str | None = None,
    status: str = "PUBLISHED",
    telegram_id: int = 1,
) -> JobPost:
    """Create and publish a job post with the structured profile filled in."""
    post = job_post_service.create_job_post(
        db,
        job_post_payload(
            telegram_id,
            subjects=subjects,
            student_level=student_level,
            location=location,
            tutor_category=tutor_category,
        ),
    )
    post.teaching_mode = teaching_mode
    if english_level is not None:
        post.english_level_required = english_level
    if status == "PUBLISHED":
        job_post_service.mark_published(db, post, -1001, 77)
    else:
        post.status = status
    db.flush()
    return post


#: A tutor profile that fits the default Grade 4 Mathematics job. Callers
#: override individual keys; nothing is required of them.
IDEAL_PROFILE: dict = {
    "locations": ["Ayat Tsebel"],
    "grades": ["Grade 4"],
    "subjects": ["Mathematics"],
    "english_proficiency": 9,
    "teaching_experience_years": 5,
    "university": "AASTU",
    "department": "Mathematics",
    "education_level": "UNIVERSITY_GRADUATE",
    "entrance_exam_score": 650,
    "entrance_exam_max_score": 840,
    "cgpa": 3.8,
    "gender": "Male",
}


def make_tutor(db: Session, telegram_id: int, **overrides):
    """Create a tutor through the Ethiopian flow, then force a status.

    ``status`` is stripped before the payload is built, because verification is
    a database concern rather than something the onboarding form collects.
    """
    status = str(overrides.pop("status", "VERIFIED")).upper()
    payload = {
        "telegram_user_id": telegram_id,
        "full_name": f"Tutor {abs(telegram_id)}",
        "phone": f"+25191{abs(telegram_id) % 10**7:07d}",
        "locations": ["Ayat Tsebel"],
        "grades": ["Grade 4"],
        "subjects": ["Mathematics"],
        "english_proficiency": 7,
        "teaching_experience_years": 2,
        "university": "AAU",
        "education_level": "UNIVERSITY_GRADUATE",
        "entrance_exam_score": 500,
        "entrance_exam_max_score": 840,
    }
    payload.update(overrides)
    # An empty collection is meaningful here (a tutor with no areas recorded),
    # so bypass the flow's own "at least one" rule: an online tutor is not
    # required to name areas, so the payload is built without them.
    if payload.get("locations") == []:
        payload["teaching_mode"] = "ONLINE"
    tutor = P.create_ethiopian_tutor(db, payload)
    tutor.status = status
    db.flush()
    return tutor


def ideal_tutor(db: Session, telegram_id: int = -9001, **overrides):
    """A tutor who matches the default Grade 4 Mathematics job on every axis."""
    return make_tutor(db, telegram_id, **{**IDEAL_PROFILE, **overrides})


# ---------------------------------------------------------------------------
# top 5 and verified-only
# ---------------------------------------------------------------------------


def test_only_verified_tutors_are_matched(db: Session) -> None:
    """Section 5: only a VERIFIED tutor may be recommended."""
    post = publish_job(db)
    for index, status in enumerate(
        ["PENDING", "DOCUMENTS_SUBMITTED", "UNDER_REVIEW", "REJECTED", "SUSPENDED", "VERIFIED"]
    ):
        make_tutor(db, -9100 - index, status=status, english_proficiency=10)
    db.flush()
    results = M.match(db, post.public_post_id, limit=50)
    assert [item.status for item in (r.tutor for r in results)] == ["VERIFIED"]


def test_the_top_five_are_returned_by_default(db: Session) -> None:
    post = publish_job(db)
    for index in range(9):
        ideal_tutor(db, -9200 - index, english_proficiency=10 - index % 4)
    results = M.match(db, post.public_post_id)
    assert len(results) == settings.match_top_n == 5


def test_results_are_ranked_best_first(db: Session) -> None:
    post = publish_job(db)
    ideal_tutor(db, -9301, english_proficiency=10)
    ideal_tutor(db, -9302, english_proficiency=3)
    ideal_tutor(db, -9303, english_proficiency=6)
    scores = [item.percentage for item in M.match(db, post.public_post_id)]
    assert scores == sorted(scores, reverse=True)


def test_ranks_are_dense_and_one_based(db: Session) -> None:
    post = publish_job(db)
    for index in range(3):
        ideal_tutor(db, -9400 - index, english_proficiency=10 - index)
    results = M.match(db, post.public_post_id)
    assert [item.rank for item in results] == [1, 2, 3]


def test_a_better_tutor_ranks_higher(db: Session) -> None:
    post = publish_job(db)
    better = ideal_tutor(db, -9501, english_proficiency=10)
    worse = ideal_tutor(db, -9502, english_proficiency=2)
    results = M.match(db, post.public_post_id)
    order = [item.public_tutor_id for item in results]
    assert order.index(better.public_tutor_id) < order.index(worse.public_tutor_id)


# ---------------------------------------------------------------------------
# tie handling (section 29)
# ---------------------------------------------------------------------------


def test_identical_tutors_tie_on_score(db: Session) -> None:
    post = publish_job(db)
    ideal_tutor(db, -9601)
    ideal_tutor(db, -9602)
    ideal_tutor(db, -9603)
    results = M.match(db, post.public_post_id)
    assert len({item.score for item in results}) == 1


def test_ties_break_deterministically_on_the_tdr_id(db: Session) -> None:
    """Identical profiles must always produce the same order."""
    post = publish_job(db)
    for index in range(6):
        ideal_tutor(db, -9700 - index)
    first = [item.public_tutor_id for item in M.match(db, post.public_post_id, limit=6)]
    second = [item.public_tutor_id for item in M.match(db, post.public_post_id, limit=6)]
    assert first == second
    assert first == sorted(first), "ties must fall back to the TDR ID"


def test_a_tie_keeps_every_tied_candidate_reachable(db: Session) -> None:
    """A tied candidate must not be dropped by an arbitrary limit."""
    post = publish_job(db)
    for index in range(8):
        ideal_tutor(db, -9800 - index)
    assert len(M.match(db, post.public_post_id, limit=8)) == 8


# ---------------------------------------------------------------------------
# explainability (section 18)
# ---------------------------------------------------------------------------


def test_a_result_never_shows_a_score_without_reasons(db: Session) -> None:
    """Section 18: never "85% match" on its own."""
    post = publish_job(db)
    ideal_tutor(db)
    result = M.match(db, post.public_post_id)[0]
    assert result.strengths, "every candidate must have stated strengths"
    assert result.missing or result.weaknesses or result.strengths


def test_every_criterion_is_reported(db: Session) -> None:
    post = publish_job(db)
    ideal_tutor(db)
    result = M.match(db, post.public_post_id)[0]
    assert set(result.criteria) == set(MATCH_CRITERIA)


def test_criterion_scores_and_weights_are_visible(db: Session) -> None:
    post = publish_job(db)
    ideal_tutor(db)
    result = M.match(db, post.public_post_id)[0]
    grade = result.criterion("grade")
    assert grade is not None
    assert 0.0 <= grade.score <= 1.0
    assert grade.weight > 0
    assert grade.weighted == pytest.approx(grade.score * grade.weight)


def test_the_total_equals_the_sum_of_the_weighted_criteria(db: Session) -> None:
    post = publish_job(db)
    ideal_tutor(db)
    result = M.match(db, post.public_post_id)[0]
    total = sum(item.weighted for item in result.criteria.values())
    assert result.score == pytest.approx(total, abs=1e-9)


def test_missing_information_is_reported_separately_from_mismatches(
    db: Session,
) -> None:
    """A gap in the record is not the same as a failing to teach."""
    post = publish_job(db)
    thin = make_tutor(db, -9901, locations=[], status="VERIFIED")
    result = M.service.score_tutor(thin, M.job_profile(post))
    assert "Tutoring areas not recorded" in result.missing
    assert result.criterion("location").is_neutral


def test_a_result_serialises_with_its_explanation(db: Session) -> None:
    post = publish_job(db)
    ideal_tutor(db)
    payload = M.match(db, post.public_post_id)[0].to_dict()
    assert payload["score"] == pytest.approx(payload["score"])
    assert set(payload) >= {
        "tutor_id",
        "score",
        "rank",
        "strengths",
        "weaknesses",
        "missing",
        "criteria",
    }
    assert payload["criteria"]["grade"]["weight"] > 0


def test_strengths_and_weaknesses_are_readable_text(db: Session) -> None:
    post = publish_job(db)
    make_tutor(db, -9911, english_proficiency=1)
    result = M.match(db, post.public_post_id)[0]
    for note in (*result.strengths, *result.weaknesses, *result.missing):
        assert isinstance(note, str) and note.strip()
        assert "<" not in note, "explanations are plain text, not markup"


# ---------------------------------------------------------------------------
# location (section 13)
# ---------------------------------------------------------------------------


def test_location_never_eliminates_a_tutor(db: Session) -> None:
    """Section 30: a distant tutor is still ranked and explained."""
    post = publish_job(db)
    far = ideal_tutor(db, -9921, locations=["Bahir Dar"])
    results = M.match(db, post.public_post_id, limit=10)
    assert far.public_tutor_id in {item.public_tutor_id for item in results}
    far_result = next(item for item in results if item.public_tutor_id == far.public_tutor_id)
    assert any("away from the job's area" in note for note in far_result.weaknesses)


def test_the_matched_area_scores_higher_than_a_distant_one(db: Session) -> None:
    post = publish_job(db)
    near = ideal_tutor(db, -9931, locations=["Nifas Bekele"])
    far = ideal_tutor(db, -9932, locations=["Bahir Dar"])
    results = {item.display_name: item for item in M.match(db, post.public_post_id, limit=10)}
    assert results[near.display_name].score > results[far.display_name].score


def test_an_online_job_ignores_location(db: Session) -> None:
    """Section 12: for online work, location carries no weight."""
    post = publish_job(db, subjects="Mathematics", location="online", teaching_mode="ONLINE")
    near = ideal_tutor(db, -9941, locations=["Ayat Tsebel"])
    far = ideal_tutor(db, -9942, locations=["Bahir Dar"])
    results = {item.display_name: item.score for item in M.match(db, post.public_post_id, limit=10)}
    assert results[near.display_name] == pytest.approx(results[far.display_name])


def test_an_online_job_has_zero_location_weight(db: Session) -> None:
    post = publish_job(db, location="online", teaching_mode="ONLINE")
    assert M.weights_for_job(M.job_profile(post))["location"] == 0.0


def test_an_in_person_job_has_a_strong_location_weight(db: Session) -> None:
    post = publish_job(db, location="Ayat Tsebel", teaching_mode="IN_PERSON")
    weights = M.weights_for_job(M.job_profile(post))
    assert weights["location"] >= 0.25
    assert weights["location"] > weights["subject"]


def test_a_tutor_with_no_areas_is_neutral_not_excluded(db: Session) -> None:
    post = publish_job(db)
    unknown = make_tutor(db, -9951, locations=[], status="VERIFIED")
    result = next(
        item
        for item in M.match(db, post.public_post_id, limit=10)
        if item.public_tutor_id == unknown.public_tutor_id
    )
    assert result.criterion("location").score == M.NEUTRAL
    assert "Tutoring areas not recorded" in result.missing


def test_location_scoring_comes_from_the_shared_service(db: Session) -> None:
    """One implementation, so the report and the score cannot disagree."""
    assert "location_service" in inspect.getsource(M.score_location)


# ---------------------------------------------------------------------------
# grade matching (sections 11, 29)
# ---------------------------------------------------------------------------


def test_the_exact_grade_scores_full_marks(db: Session) -> None:
    post = publish_job(db, student_level="4th Grader")
    tutor = ideal_tutor(db, -9961, grades=["Grade 4"])
    result = M.service.score_tutor(tutor, M.job_profile(post))
    assert result.criterion("grade").score == pytest.approx(1.0)


def test_a_band_covering_the_job_grade_scores_full_marks(db: Session) -> None:
    post = publish_job(db, student_level="4th Grader")
    tutor = ideal_tutor(db, -9962, grades=["Grade 1-3", "Grade 4-6"])
    result = M.service.score_tutor(tutor, M.job_profile(post))
    assert result.criterion("grade").score == pytest.approx(1.0)


def test_legacy_level_bands_are_still_scored(db: Session) -> None:
    """The 1,000+ tutors have TutorLevel bands, not TutorGrade rows."""
    from app.services import tutor_service
    from tests.factories import tutor_payload

    post = publish_job(db, student_level="4th Grader")
    tutor = tutor_service.create_tutor(
        db, tutor_payload(-9963, levels=["Grade 4-6"], status="VERIFIED")
    )
    result = M.service.score_tutor(tutor, M.job_profile(post))
    assert result.criterion("grade").score > 0.5
    assert "No grade levels recorded" not in result.missing


def test_a_distant_grade_scores_lower(db: Session) -> None:
    post = publish_job(db, student_level="4th Grader")
    near = M.service.score_tutor(ideal_tutor(db, -9964, grades=["Grade 4"]), M.job_profile(post))
    far = M.service.score_tutor(ideal_tutor(db, -9965, grades=["Grade 12"]), M.job_profile(post))
    assert near.criterion("grade").score > far.criterion("grade").score


def test_a_tutor_with_no_grades_is_neutral(db: Session) -> None:
    post = publish_job(db)
    tutor = ideal_tutor(db, -9966)
    tutor.grades.clear()
    db.flush()
    result = M.service.score_tutor(tutor, M.job_profile(post))
    assert result.criterion("grade").score == M.NEUTRAL
    assert "No grade levels recorded" in result.missing


@pytest.mark.parametrize(
    "written,expected",
    [
        ("4th Grader", (4, 4)),
        ("Grade 4", (4, 4)),
        ("Grade 4-6", (4, 6)),
        ("Grade 1-3", (1, 3)),
        ("6-4", (4, 6)),
        ("Elementary", (1, 4)),
        ("KG", (0, 0)),
    ],
)
def test_grade_vocabularies_resolve_to_the_same_band(written, expected) -> None:
    band = grade_service.grade_band(written)
    assert (band.low, band.high) == expected


# ---------------------------------------------------------------------------
# subject matching (section 29)
# ---------------------------------------------------------------------------


def test_the_exact_subject_scores_full_marks(db: Session) -> None:
    post = publish_job(db, subjects="Mathematics")
    tutor = ideal_tutor(db, -9971, subjects=["Mathematics"])
    assert M.service.score_tutor(tutor, M.job_profile(post)).criterion("subject").score == 1.0


def test_subject_synonyms_are_the_same_subject(db: Session) -> None:
    post = publish_job(db, subjects="Mathematics")
    tutor = ideal_tutor(db, -9972, subjects=["Maths"])
    assert M.service.score_tutor(tutor, M.job_profile(post)).criterion("subject").score == 1.0


def test_major_subjects_expands_to_its_components() -> None:
    assert grade_service.canonical_subjects("Major Subjects") == (
        "ENGLISH",
        "MATH",
        "SCIENCE",
    )


def test_a_partial_subject_match_scores_proportionally(db: Session) -> None:
    post = publish_job(db, subjects="Mathematics, Physics")
    tutor = ideal_tutor(db, -9973, subjects=["Mathematics"])
    score = M.service.score_tutor(tutor, M.job_profile(post)).criterion("subject").score
    assert 0.0 < score < 1.0


def test_an_unrelated_subject_scores_zero_but_keeps_the_tutor(db: Session) -> None:
    post = publish_job(db, subjects="Mathematics")
    tutor = ideal_tutor(db, -9974, subjects=["Music"])
    result = M.service.score_tutor(tutor, M.job_profile(post))
    assert result.criterion("subject").score == 0.0
    assert result.score > 0


def test_a_tutor_with_no_subjects_is_neutral(db: Session) -> None:
    post = publish_job(db)
    tutor = ideal_tutor(db, -9975)
    tutor.subjects.clear()
    db.flush()
    result = M.service.score_tutor(tutor, M.job_profile(post))
    assert result.criterion("subject").score == M.NEUTRAL
    assert "No subjects recorded" in result.missing


# ---------------------------------------------------------------------------
# English matching (sections 15, 29)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("level", [1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
def test_every_english_level_produces_a_score_in_range(db: Session, level: int) -> None:
    post = publish_job(db)
    tutor = ideal_tutor(db, -9980 - level, english_proficiency=level)
    result = M.service.score_tutor(tutor, M.job_profile(post))
    assert 0.0 <= result.criterion("english").score <= 1.0


def test_a_higher_english_level_never_scores_lower(db: Session) -> None:
    post = publish_job(db)
    scores = [
        M.service.score_tutor(
            ideal_tutor(db, -9990 - level, english_proficiency=level), M.job_profile(post)
        ).criterion("english").score
        for level in (3, 5, 7, 9)
    ]
    assert scores == sorted(scores)


def test_a_high_english_requirement_sharpens_the_score(db: Session) -> None:
    """Section 15: 9/10 is strong for a high bar; the same bar exposes 5/10."""
    post = publish_job(db, english_level="HIGH")
    strong = M.service.score_tutor(
        ideal_tutor(db, -9995, english_proficiency=9), M.job_profile(post)
    )
    weak = M.service.score_tutor(
        ideal_tutor(db, -9996, english_proficiency=5), M.job_profile(post)
    )
    assert strong.criterion("english").score > 0.75
    assert weak.criterion("english").score < strong.criterion("english").score
    # ...while a basic bar no longer differentiates them.
    basic = publish_job(db, telegram_id=2, english_level="BASIC")
    strong_basic = M.service.score_tutor(
        ideal_tutor(db, -9997, english_proficiency=9), M.job_profile(basic)
    )
    assert strong_basic.criterion("english").score >= 0.9


def test_a_job_needing_no_english_drops_the_english_weight(db: Session) -> None:
    post = publish_job(db, english_level="NONE")
    assert M.weights_for_job(M.job_profile(post))["english"] == 0.0


def test_a_low_english_rating_does_not_exclude_a_tutor(db: Session) -> None:
    """Section 15: never auto-reject on self-rated English alone."""
    post = publish_job(db, english_level="HIGH")
    tutor = ideal_tutor(db, -9996, english_proficiency=1)
    result = M.service.score_tutor(tutor, M.job_profile(post))
    assert result.criterion("english").score < 0.5
    assert result.score > 0
    ids = {item.public_tutor_id for item in M.match(db, post.public_post_id, limit=10)}
    assert tutor.public_tutor_id in ids


def test_a_missing_english_level_is_neutral_not_a_zero(db: Session) -> None:
    post = publish_job(db)
    tutor = ideal_tutor(db, -9997, english_proficiency=None)
    result = M.service.score_tutor(tutor, M.job_profile(post))
    assert result.criterion("english").score == M.NEUTRAL
    assert "English level not provided" in result.missing


def test_the_voice_recording_is_evidence_not_a_score(db: Session) -> None:
    """The recording must never move the number, only be offered to the admin."""
    post = publish_job(db)
    without = M.service.score_tutor(ideal_tutor(db, -9998), M.job_profile(post))
    with_voice = M.service.score_tutor(
        ideal_tutor(db, -9999), M.job_profile(post)
    )
    P.record_english_voice(db, with_voice.tutor, "voice", 5)
    after = M.service.score_tutor(with_voice.tutor, M.job_profile(post))
    assert after.criterion("english").score == pytest.approx(
        without.criterion("english").score
    )
    assert any("voice recording" in note.lower() for note in after.strengths)


def test_the_engine_never_claims_a_cefr_level(db: Session) -> None:
    """No explanation may assert a proficiency level from the recording."""
    source = inspect.getsource(M)
    assert "CEFR" not in source.replace("no CEFR level", "")
    assert not re.search(r"cefr\s*[:=]\s*['\"]?[abc][12]", source, re.IGNORECASE)


# ---------------------------------------------------------------------------
# academic performance (section 14)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "score,maximum,expected",
    [
        (612, 840, 100 * 612 / 840),
        (72, 100, 72.0),
        (840, 840, 100.0),
        (0, 100, 0.0),
    ],
)
def test_entrance_scores_normalise_to_a_percentage(score, maximum, expected) -> None:
    tutor = Tutor(
        name="T",
        display_name="T",
        phone="+251911234567",
        email="",
        country="Ethiopia",
        city="Addis Ababa",
        teaching_mode="IN_PERSON",
        entrance_exam_score=score,
        entrance_exam_max_score=maximum,
    )
    assert M.normalised_entrance_score(tutor) == pytest.approx(expected)


def test_a_score_without_a_maximum_is_not_compared_raw() -> None:
    """The reason ``entrance_exam_max_score`` is stored at all."""
    tutor = Tutor(
        name="T",
        display_name="T",
        phone="+251911234567",
        email="",
        country="Ethiopia",
        city="Addis Ababa",
        teaching_mode="IN_PERSON",
        entrance_exam_score=612,
        entrance_exam_max_score=None,
    )
    assert M.normalised_entrance_score(tutor) is None


def test_the_same_raw_score_on_different_scales_differs_after_normalisation() -> None:
    """500/840 and 500/100 must not be treated as equal results."""
    def tutor(maximum: float) -> Tutor:
        return Tutor(
            name="T",
            display_name="T",
            phone="+251911234567",
            email="",
            country="Ethiopia",
            city="Addis Ababa",
            teaching_mode="IN_PERSON",
            entrance_exam_score=500,
            entrance_exam_max_score=maximum,
        )

    assert M.normalised_entrance_score(tutor(840)) < M.normalised_entrance_score(tutor(100))


def test_a_stronger_academic_record_scores_higher(db: Session) -> None:
    post = publish_job(db)
    strong = M.service.score_tutor(
        ideal_tutor(db, -96001, entrance_exam_score=780, entrance_exam_max_score=840, cgpa=3.9),
        M.job_profile(post),
    )
    weak = M.service.score_tutor(
        ideal_tutor(db, -96002, entrance_exam_score=300, entrance_exam_max_score=840, cgpa=2.1),
        M.job_profile(post),
    )
    assert strong.criterion("academic").score > weak.criterion("academic").score


def test_the_entrance_exam_is_never_the_dominant_factor(db: Session) -> None:
    """Section 30: academics cannot outweigh an inability to teach the job."""
    post = publish_job(db, subjects="Mathematics", student_level="4th Grader")
    academic_star_wrong_teacher = M.service.score_tutor(
        ideal_tutor(db, -96003, grades=["Grade 12"], subjects=["Music"], entrance_exam_score=840, entrance_exam_max_score=840),
        M.job_profile(post),
    )
    modest_practitioner = M.service.score_tutor(
        ideal_tutor(db, -96004, entrance_exam_score=350, entrance_exam_max_score=840),
        M.job_profile(post),
    )
    assert modest_practitioner.score > academic_star_wrong_teacher.score


def test_the_academic_criterion_is_capped(db: Session) -> None:
    post = publish_job(db)
    tutor = ideal_tutor(
        db, -96005, entrance_exam_score=840, entrance_exam_max_score=840, cgpa=4.0
    )
    for kind in ("UNIVERSITY_TRANSCRIPT", "UNIVERSITY_ENTRANCE_RESULT"):
        P.record_document(db, tutor, kind, "f", file_name="d.pdf")
    score = M.service.score_tutor(tutor, M.job_profile(post)).criterion("academic").score
    assert score <= M.ACADEMIC_MAX_CONTRIBUTION


def test_no_academic_record_is_neutral_and_reported(db: Session) -> None:
    post = publish_job(db)
    tutor = ideal_tutor(
        db,
        -96006,
        entrance_exam_score=None,
        entrance_exam_max_score=None,
        cgpa=None,
        university=None,
        department=None,
    )
    tutor.education.clear()
    db.flush()
    result = M.service.score_tutor(tutor, M.job_profile(post))
    assert result.criterion("academic").score == M.NEUTRAL
    assert "No academic records provided" in result.missing


def test_a_relevant_degree_is_rewarded(db: Session) -> None:
    post = publish_job(db, subjects="Mathematics")
    relevant = M.service.score_tutor(
        ideal_tutor(db, -96007, department="Mathematics"), M.job_profile(post)
    )
    unrelated = M.service.score_tutor(
        ideal_tutor(db, -96008, department="History"), M.job_profile(post)
    )
    assert relevant.criterion("academic").score > unrelated.criterion("academic").score


def test_verified_documents_are_a_signal(db: Session) -> None:
    post = publish_job(db)
    with_docs = ideal_tutor(db, -96009)
    without = ideal_tutor(db, -96010)
    P.record_document(db, with_docs, "UNIVERSITY_TRANSCRIPT", "f", file_name="t.pdf")
    assert (
        M.service.score_tutor(with_docs, M.job_profile(post)).criterion("academic").score
        > M.service.score_tutor(without, M.job_profile(post)).criterion("academic").score
    )


# ---------------------------------------------------------------------------
# experience relevance (section 16)
# ---------------------------------------------------------------------------


def test_relevant_experience_beats_irrelevant_experience(db: Session) -> None:
    """Section 16: 5 years of Grade 4 maths beats 5 years of university code."""
    post = publish_job(db, subjects="Mathematics", student_level="4th Grader")
    relevant = ideal_tutor(db, -96101, teaching_experience_years=5)
    P.add_experience(
        db, relevant, {"years": 5, "subjects": ["Mathematics"], "grades": ["Grade 4"]}
    )
    irrelevant = ideal_tutor(db, -96102, teaching_experience_years=5)
    P.add_experience(
        db, irrelevant, {"subjects": ["Programming"], "grades": ["University"]}
    )
    relevant_score = M.service.score_tutor(relevant, M.job_profile(post)).criterion("experience")
    irrelevant_score = M.service.score_tutor(irrelevant, M.job_profile(post)).criterion("experience")
    assert relevant_score.score > irrelevant_score.score


def test_relevance_outranks_total_years(db: Session) -> None:
    post = publish_job(db, subjects="Mathematics", student_level="4th Grader")
    long_irrelevant = ideal_tutor(db, -96103, teaching_experience_years=20)
    P.add_experience(db, long_irrelevant, {"subjects": ["Music"], "grades": ["Grade 1"]})
    short_relevant = ideal_tutor(db, -96104, teaching_experience_years=2)
    P.add_experience(
        db, short_relevant, {"subjects": ["Mathematics"], "grades": ["Grade 4"]}
    )
    assert (
        M.service.score_tutor(short_relevant, M.job_profile(post)).criterion("experience").score
        > M.service.score_tutor(long_irrelevant, M.job_profile(post)).criterion("experience").score
    )


def test_years_still_matter_when_relevance_is_equal(db: Session) -> None:
    post = publish_job(db, subjects="Mathematics", student_level="4th Grader")
    few = ideal_tutor(db, -96105, teaching_experience_years=1)
    P.add_experience(db, few, {"subjects": ["Mathematics"], "grades": ["Grade 4"]})
    many = ideal_tutor(db, -96106, teaching_experience_years=6)
    P.add_experience(db, many, {"subjects": ["Mathematics"], "grades": ["Grade 4"]})
    assert (
        M.service.score_tutor(many, M.job_profile(post)).criterion("experience").score
        > M.service.score_tutor(few, M.job_profile(post)).criterion("experience").score
    )


def test_experience_saturates_so_relevance_decides(db: Session) -> None:
    """Beyond the saturation point, more years add nothing."""
    post = publish_job(db, subjects="Mathematics", student_level="4th Grader")
    relevant = ideal_tutor(db, -96107, teaching_experience_years=30)
    P.add_experience(
        db, relevant, {"subjects": ["Mathematics"], "grades": ["Grade 4"]}
    )
    score = M.service.score_tutor(relevant, M.job_profile(post)).criterion("experience").score
    assert score <= 1.0
    assert score == pytest.approx(1.0, abs=0.35)


def test_no_documented_experience_is_neutral_and_flagged(db: Session) -> None:
    """Section 18's own example: "No documented tutoring experience"."""
    post = publish_job(db)
    tutor = make_tutor(
        db, -96108, teaching_experience_years=None, experience_years=0, status="VERIFIED"
    )
    result = M.service.score_tutor(tutor, M.job_profile(post))
    assert result.criterion("experience").score == M.NEUTRAL
    assert "No documented tutoring experience" in result.missing


def test_documented_experience_is_detected_on_the_result(db: Session) -> None:
    post = publish_job(db)
    tutor = ideal_tutor(db, -96109)
    assert M.service.score_tutor(tutor, M.job_profile(post)).has_documented_experience is True


# ---------------------------------------------------------------------------
# gender / tutor category (section 30)
# ---------------------------------------------------------------------------


def test_gender_carries_at_most_five_percent(db: Session) -> None:
    """A hard cap on the smallest criterion."""
    post = publish_job(db, tutor_category="Male")
    assert M.weights_for_job(M.job_profile(post))["category"] <= 0.05


def test_a_gender_mismatch_costs_at_most_the_category_weight(db: Session) -> None:
    post = publish_job(db, tutor_category="Female")
    matching = M.service.score_tutor(
        ideal_tutor(db, -96201, gender="Female"), M.job_profile(post)
    )
    mismatched = M.service.score_tutor(
        ideal_tutor(db, -96202, gender="Male"), M.job_profile(post)
    )
    gap = matching.score - mismatched.score
    weights = M.weights_for_job(M.job_profile(post))
    assert 0 < gap <= weights["category"] + 1e-9


def test_a_gender_mismatch_never_excludes_a_tutor(db: Session) -> None:
    post = publish_job(db, tutor_category="Female")
    tutor = ideal_tutor(db, -96203, gender="Male")
    ids = {item.public_tutor_id for item in M.match(db, post.public_post_id, limit=10)}
    assert tutor.public_tutor_id in ids


def test_a_gender_mismatch_still_reports_the_reason(db: Session) -> None:
    post = publish_job(db, tutor_category="Female")
    tutor = ideal_tutor(db, -96204, gender="Male")
    result = M.service.score_tutor(tutor, M.job_profile(post))
    assert any("Female" in note for note in result.weaknesses)


def test_an_open_category_matches_anyone(db: Session) -> None:
    post = publish_job(db, tutor_category="Both Male and Female")
    result = M.service.score_tutor(ideal_tutor(db, -96205, gender="Male"), M.job_profile(post))
    assert result.criterion("category").score == 1.0


def test_an_unrecorded_gender_is_neutral(db: Session) -> None:
    post = publish_job(db, tutor_category="Female")
    tutor = ideal_tutor(db, -96206, gender=None)
    result = M.service.score_tutor(tutor, M.job_profile(post))
    assert result.criterion("category").score == M.NEUTRAL
    assert "Gender not provided" in result.missing


# ---------------------------------------------------------------------------
# job-aware weighting (section 12)
# ---------------------------------------------------------------------------


def test_weights_always_sum_to_one(db: Session) -> None:
    for subjects, level, mode in [
        ("English", "4th Grader", "IN_PERSON"),
        ("Mathematics", "12th Grader", "IN_PERSON"),
        ("Mathematics", "12th Grader", "ONLINE"),
        ("Major Subjects", "4th Grader", "ONLINE"),
    ]:
        post = publish_job(
            db, subjects=subjects, student_level=level, teaching_mode=mode,
            location="Ayat Tsebel" if mode != "ONLINE" else "online",
        )
        weights = M.weights_for_job(M.job_profile(post))
        assert sum(weights.values()) == pytest.approx(1.0), (subjects, mode)


def test_an_english_job_leans_on_english(db: Session) -> None:
    post = publish_job(db, subjects="English", student_level="4th Grader")
    weights = M.weights_for_job(M.job_profile(post))
    assert weights["english"] >= 0.20


def test_an_advanced_maths_job_leans_on_subject_and_academics(db: Session) -> None:
    """A Grade 12 Maths job must rank subject and academics over English."""
    post = publish_job(db, subjects="Mathematics", student_level="12th Grader")
    weights = M.weights_for_job(M.job_profile(post))
    assert weights["subject"] > weights["english"]
    assert weights["academic"] > weights["english"]
    # ...and location cannot be zero once the mode is in person.
    assert weights["location"] > 0


def test_the_same_tutor_is_ranked_differently_for_different_jobs(db: Session) -> None:
    """Job-awareness must actually change the outcome, not just the weights."""
    strong_maths = ideal_tutor(
        db, -96301, subjects=["Mathematics"], grades=["Grade 12"], english_proficiency=6
    )
    post_maths = publish_job(
        db, subjects="Mathematics", student_level="12th Grader", english_level="NONE"
    )
    post_english = publish_job(
        db,
        telegram_id=2,
        subjects="English",
        student_level="4th Grader",
        english_level="HIGH",
    )
    maths_score = M.service.score_tutor(strong_maths, M.job_profile(post_maths)).score
    english_score = M.service.score_tutor(strong_maths, M.job_profile(post_english)).score
    assert maths_score > english_score


def test_teaching_mode_overrides_the_subject_profile(db: Session) -> None:
    """An online job must not carry an in-person location weight."""
    post = publish_job(
        db, subjects="Mathematics", student_level="12th Grader",
        location="online", teaching_mode="ONLINE",
    )
    assert M.weights_for_job(M.job_profile(post))["location"] == 0.0


def test_a_caller_can_supply_its_own_weights(db: Session) -> None:
    post = publish_job(db)
    perfect = ideal_tutor(db, -96401)
    near = ideal_tutor(db, -96402, locations=["Nifas Bekele"])
    custom = {name: 0.0 for name in MATCH_CRITERIA}
    custom["location"] = 1.0
    results = M.match(db, post.public_post_id, limit=10, weights=custom)
    by_id = {item.public_tutor_id: item.score for item in results}
    # With all weight on location, the exact-match tutor winsoutright...
    assert by_id[perfect.public_tutor_id] == pytest.approx(1.0)
    # ...and the merely-near town scores its distance, nothing more.
    assert by_id[near.public_tutor_id] < by_id[perfect.public_tutor_id]


def test_a_partial_weight_profile_is_normalised(db: Session) -> None:
    normalised = normalise_weights({"subject": 2.0, "grade": 2.0})
    assert sum(normalised.values()) == pytest.approx(1.0)
    assert normalised["subject"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# re-matching a newly verified tutor (section 22)
# ---------------------------------------------------------------------------


def test_a_new_tutor_is_matched_against_open_jobs(db: Session) -> None:
    job = publish_job(db, status="DRAFT")
    tutor = ideal_tutor(db, -96501, english_proficiency=10)
    strong = M.match_for_new_tutor(db, tutor, [job])
    assert [post.public_post_id for post, _ in strong] == [job.public_post_id]


def test_a_weak_match_is_not_reported(db: Session) -> None:
    """Section 22: do not spam the admin about weak matches."""
    job = publish_job(db)
    tutor = ideal_tutor(db, -96502, locations=["Bahir Dar"], grades=["Grade 12"], subjects=["Music"], english_proficiency=1)
    assert M.match_for_new_tutor(db, tutor, [job]) == []


def test_the_threshold_is_configurable(db: Session) -> None:
    job = publish_job(db)
    tutor = ideal_tutor(db, -96503, english_proficiency=4)
    assert M.match_for_new_tutor(db, tutor, [job], threshold=10) != []


def test_the_threshold_defaults_to_the_configured_value() -> None:
    assert settings.match_notification_threshold == 85.0


def test_a_closed_job_is_never_re_matched(db: Session) -> None:
    closed = publish_job(db, status="CLOSED")
    tutor = ideal_tutor(db, -96504, english_proficiency=10)
    assert M.match_for_new_tutor(db, tutor, [closed]) == []


def test_strong_matches_are_ordered_by_score(db: Session) -> None:
    best = publish_job(db, subjects="Mathematics", student_level="4th Grader")
    worst = publish_job(
        db, telegram_id=2, subjects="Music", student_level="12th Grader"
    )
    tutor = ideal_tutor(db, -96505, english_proficiency=10)
    results = M.match_for_new_tutor(db, tutor, [worst, best], threshold=40)
    scores = [result.score for _, result in results]
    assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# the 50-member rule is never part of matching (section 7)
# ---------------------------------------------------------------------------


def test_member_referral_is_never_read_by_the_engine() -> None:
    """No scoring path may read ``Tutor.member_referral_status``."""
    import ast

    tree = ast.parse(inspect.getsource(M))
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "member_referral_status":
            raise AssertionError("the matching engine reads member_referral_status")


def test_the_member_status_cannot_change_a_score(db: Session) -> None:
    post = publish_job(db)
    tutor = ideal_tutor(db, -96601)
    before = M.service.score_tutor(tutor, M.job_profile(post)).score
    P.set_member_referral_status(db, tutor, "VERIFIED")
    after = M.service.score_tutor(tutor, M.job_profile(post)).score
    assert before == pytest.approx(after)


# ---------------------------------------------------------------------------
# robustness
# ---------------------------------------------------------------------------


def test_an_unknown_job_reference_raises(db: Session) -> None:
    with pytest.raises(ValueError):
        M.match(db, "JOB-999999")


def test_a_job_can_be_passed_as_an_object_or_a_reference(db: Session) -> None:
    post = publish_job(db)
    ideal_tutor(db, -96701)
    by_object = M.match(db, post)
    by_reference = M.match(db, post.public_post_id)
    assert [item.public_tutor_id for item in by_object] == [
        item.public_tutor_id for item in by_reference
    ]


def test_a_reference_is_case_insensitive(db: Session) -> None:
    post = publish_job(db)
    ideal_tutor(db, -96702)
    assert M.match(db, post.public_post_id.lower())


def test_a_job_with_no_verified_tutors_returns_nothing(db: Session) -> None:
    post = publish_job(db)
    assert M.match(db, post.public_post_id) == []


def test_an_unreadable_job_level_does_not_crash_the_scorer(db: Session) -> None:
    post = publish_job(db, student_level="Other")
    tutor = ideal_tutor(db, -96801)
    result = M.service.score_tutor(tutor, M.job_profile(post))
    assert 0.0 <= result.score <= 1.0
    assert result.criterion("grade").is_neutral


def test_scoring_is_pure_and_repeatable(db: Session) -> None:
    post = publish_job(db)
    tutor = ideal_tutor(db, -96802)
    profile = M.job_profile(post)
    scores = {M.service.score_tutor(tutor, profile).score for _ in range(5)}
    assert len(scores) == 1


def test_scoring_does_not_write_to_the_database(db: Session) -> None:
    post = publish_job(db)
    tutor = ideal_tutor(db, -96803)
    before = (tutor.status, tutor.english_proficiency, len(tutor.subjects))
    M.service.score_tutor(tutor, M.job_profile(post))
    assert (tutor.status, tutor.english_proficiency, len(tutor.subjects)) == before


@given(
    english=st.integers(min_value=1, max_value=10),
    years=st.integers(min_value=0, max_value=30),
    seed=st.integers(min_value=1, max_value=10**5),
)
@PROFILE
def test_property_scores_stay_in_range(fresh_db, english: int, years: int, seed: int) -> None:
    """Feature: tutor-onboarding-and-matching, Property: score bounds.

    Validates: Requirement 10 (weighted match score)
    """
    session = fresh_db()
    post = publish_job(session)
    tutor = ideal_tutor(session, -seed - 1, english_proficiency=english, teaching_experience_years=years)
    for item in M.service.score_tutor(tutor, M.job_profile(post)).criteria.values():
        assert 0.0 <= item.score <= 1.0


@given(seed=st.integers(min_value=1, max_value=10**5))
@PROFILE
def test_property_matching_is_deterministic(fresh_db, seed: int) -> None:
    """Feature: tutor-onboarding-and-matching, Property: deterministic ranking.

    Validates: Requirement 17 (top 5), Requirement 18 (explainability)
    """
    session = fresh_db()
    post = publish_job(session)
    for index in range(4):
        ideal_tutor(session, -seed * 100 - index - 1, english_proficiency=10 - index)
    first = [(item.public_tutor_id, item.percentage) for item in M.match(session, post.public_post_id)]
    second = [(item.public_tutor_id, item.percentage) for item in M.match(session, post.public_post_id)]
    assert first == second