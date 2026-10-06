"""Job-candidate persistence and the automatic matching pipeline.

Feature: tutor-onboarding-and-matching, §17–§21
Validates: Requirement 17 (top 5), Requirement 19 (admin actions, candidate
lifecycle), Requirement 20 (automatic workflow), Requirement 21 (/match)
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.enums import CandidateStatus
from app.models import JobCandidate, JobPost, Tutor
from app.services import job_match_pipeline as pipeline
from app.services import job_post_service, tutor_profile_service as P
from tests.factories import job_post_payload
from tests.test_matching_service import ideal_tutor, publish_job


def _job(db: Session) -> JobPost:
    post = publish_job(db)
    return post


# ---------------------------------------------------------------------------
# offer / persistence
# ---------------------------------------------------------------------------


def test_candidates_are_persisted_with_rank_and_score(db: Session) -> None:
    post = _job(db)
    for index in range(3):
        ideal_tutor(db, -7100 - index)
    results = pipeline.run_match_for_job(db, post)
    rows = db.query(JobCandidate).filter_by(job_post_id=post.id).order_by(JobCandidate.rank).all()
    assert len(rows) == len(results) == 3
    assert [row.rank for row in rows] == [1, 2, 3]
    for row, result in zip(rows, results):
        assert row.match_score == pytest.approx(result.percentage)
        assert row.status == str(CandidateStatus.RECOMMENDED)
        assert row.matched_at is not None


def test_rerunning_replaces_stale_rows(db: Session) -> None:
    post = _job(db)
    ideal_tutor(db, -7201)
    pipeline.run_match_for_job(db, post)
    assert db.query(JobCandidate).filter_by(job_post_id=post.id).count() == 1

    # A second tutor arrives — a rerun must replace, not duplicate.
    ideal_tutor(db, -7202)
    pipeline.run_match_for_job(db, post)
    assert db.query(JobCandidate).filter_by(job_post_id=post.id).count() == 2


def test_only_verified_tutors_become_candidates(db: Session) -> None:
    post = _job(db)
    ideal_tutor(db, -7301, status="PENDING")
    ideal_tutor(db, -7302)
    pipeline.run_match_for_job(db, post)
    rows = db.query(JobCandidate).join(Tutor).filter(JobCandidate.job_post_id == post.id).all()
    assert len(rows) == 1
    assert rows[0].tutor.status == "VERIFIED"


def test_zero_candidates_when_nothing_matches(db: Session) -> None:
    post = _job(db)
    assert pipeline.run_match_for_job(db, post) == []
    assert db.query(JobCandidate).filter_by(job_post_id=post.id).count() == 0


def test_candidate_details_roundtrip_the_explanation(db: Session) -> None:
    post = _job(db)
    ideal_tutor(db, -7401)
    pipeline.run_match_for_job(db, post)
    row = db.query(JobCandidate).one()
    details = pipeline.candidate_details(row)
    assert details["rank"] == 1
    assert details["strengths"], "the persisted explanation must not be empty"
    assert "grade" in details["criteria"]


def test_candidate_status_lifecycle_is_admin_driven(db: Session) -> None:
    post = _job(db)
    tutor = ideal_tutor(db, -7501)
    pipeline.run_match_for_job(db, post)

    candidate = pipeline.set_candidate_status(db, post.public_post_id, tutor.public_tutor_id, "SHORTLISTED")
    assert candidate is not None
    assert candidate.status == "SHORTLISTED"

    candidate = pipeline.set_candidate_status(db, post.public_post_id, tutor.public_tutor_id, "SELECTED")
    assert candidate.status == "SELECTED"


def test_an_unknown_candidate_status_change_is_a_noop(db: Session) -> None:
    post = _job(db)
    assert pipeline.set_candidate_status(db, post.public_post_id, "TDR-000999", "SELECTED") is None
    assert pipeline.set_candidate_status(db, "JOB-999999", "TDR-000001", "SELECTED") is None


def test_status_change_does_not_insert_a_new_recommendation(db: Session) -> None:
    """A shortlist cannot materialise a match that never ran."""
    post = _job(db)
    assert pipeline.candidate_for(db, post.public_post_id, "TDR-999999") is None
    assert pipeline.set_candidate_status(db, post.public_post_id, "TDR-999999", "SELECTED") is None
    assert db.query(JobCandidate).count() == 0


# ---------------------------------------------------------------------------
# report rendering (section 17 format)
# ---------------------------------------------------------------------------


def test_report_contains_the_headline_and_medals(db: Session) -> None:
    post = _job(db)
    for index in range(3):
        ideal_tutor(db, -7600 - index)
    results = pipeline.run_match_for_job(db, post)
    report = pipeline.format_report(post, results)
    assert "TOP TUTOR MATCHES" in report
    assert "🥇" in report and "🥈" in report
    assert "Grade 4" in report or "4th Grader" in report
    assert "Why recommended" in report


def test_report_is_plain_and_escaped_for_telegram(db: Session) -> None:
    post = _job(db)
    tutor = ideal_tutor(db, -7701)
    tutor.display_name = "A <b> bold </b> name"
    results = pipeline.run_match_for_job(db, post)
    card = pipeline.format_candidate_card(results[0])
    assert "<code>" in card  # our formatting tags
    assert "&lt;b&gt; bold &lt;/b&gt;" in card, "admin-entered text must be HTML-escaped"


def test_report_line_for_a_candidate_lists_present_and_missing(db: Session) -> None:
    post = _job(db)
    ideal_tutor(db, -7801)
    results = pipeline.run_match_for_job(db, post)
    card = pipeline.format_candidate_card(results[0])
    assert "📍 Location:" in card
    assert "🎓 Grade:" in card and "📚 Subject:" in card
    assert "🇬🇧 English:" in card
    assert "🎓 Entrance exam:" in card
    assert "👨‍🏫 Experience:" in card


def test_empty_match_produces_a_helpful_report(db: Session) -> None:
    post = _job(db)
    report = pipeline.format_report(post, [])
    assert "No verified tutors matched" in report


def test_reason_lines_lead_the_admin_to_judge(db: Session) -> None:
    post = _job(db)
    ideal_tutor(db, -7901, english_proficiency=2)
    results = pipeline.run_match_for_job(db, post)
    card = pipeline.format_candidate_card(results[0])
    assert ("Weak" in card) or ("Missing information" in card)


# ---------------------------------------------------------------------------
# re-match API surface
# ---------------------------------------------------------------------------


def test_open_jobs_excludes_closed(db: Session) -> None:
    open_post = publish_job(db, status="DRAFT")
    closed = publish_job(db, telegram_id=2, status="CLOSED")
    open_ids = {post.public_post_id for post in pipeline.open_jobs(db)}
    assert open_post.public_post_id in open_ids
    assert closed.public_post_id not in open_ids


def test_rerun_after_closing_the_candidate_set(db: Session) -> None:
    post = _job(db)
    ideal_tutor(db, -8001)
    pipeline.run_match_for_job(db, post)
    post.status = "CLOSED"
    assert pipeline.get_job_candidates(db, post.public_post_id), "history of a closed job may remain"


def test_candidate_details_is_resilient_to_bad_json() -> None:
    row = JobCandidate(job_post_id=1, tutor_id=1, match_score=0, rank=0, status="RECOMMENDED")
    row.details = "not json{{"
    assert pipeline.candidate_details(row) == {}


def test_format_report_bundles_job_header_and_cards(db: Session) -> None:
    post = _job(db)
    ideal_tutor(db, -8101)
    results = pipeline.run_match_for_job(db, post)
    report = pipeline.format_report(post, results, threshold_note="Minimum score for a report: 50%")
    assert post.location in report
    assert "Minimum score for a report: 50%" in report


# ---------------------------------------------------------------------------
# profile completeness surface (§27)
# ---------------------------------------------------------------------------


def test_completeness_report_shape(db: Session) -> None:
    tutor = ideal_tutor(db, -8201)
    report = P.completion_report(tutor)
    assert set(report) >= {"tdr_id", "percentage", "missing", "missing_labels", "complete"}
    assert report["tdr_id"] == tutor.public_tutor_id
    assert 0 <= report["percentage"] <= 100


def test_imported_tutor_data_produces_not_invented_missing_fields(db: Session) -> None:
    from app.services import tutor_service
    from tests.factories import tutor_payload

    legacy = tutor_service.create_tutor(db, tutor_payload(-8301))
    labels = P.missing_profile_labels(legacy)
    assert labels, "imported tutors must show what they lack"
    # The stored values are never fabricated to fill the report.
    assert P.profile_completion_percentage(legacy) < 100