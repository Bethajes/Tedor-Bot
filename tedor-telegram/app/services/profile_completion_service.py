"""Server-side profile-completion validation for the Apply flow.

The tutor application form (:mod:`app.bot.handlers.tutor_onboarding`) is the
single source of truth for what a tutor profile contains. This module does not
re-define those fields: it reads the ones
:mod:`app.services.tutor_profile_service` already publishes and turns them into
one decision the Apply flow can branch on — complete, incomplete, awaiting
verification, or refused.

Three rules shape everything here:

* **Never trust the caller.** The assessment is computed from the stored
  ``Tutor`` row every time. No value arrives from callback data, a deep-link
  payload, or a claim the tutor made about their own progress.
* **Missing is not invalid, and unknown is not zero.** A blank field is asked
  for again; a field holding a value outside its permitted range is reported as
  invalid so the tutor can correct it. Absent entrance-exam data stays absent —
  the matching engine already scores that as :data:`NEUTRAL`, and this module
  never converts "we don't know" into a failing grade.
* **Documents are conditional.** An undergraduate and a master's graduate are
  asked for different academic documents, via
  :func:`~app.services.tutor_profile_service.academic_document_types_for`.
  No tutor is ever asked for every document in the catalogue.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.config import settings
from app.enums import DocumentType, EducationLevel
from app.models import Tutor
from app.services import tutor_profile_service as profile_service

logger = logging.getLogger(__name__)


class ProfileState(StrEnum):
    """How a stored tutor profile stands relative to submitting an application."""

    #: No tutor record exists for this Telegram user yet.
    NOT_STARTED = "NOT_STARTED"
    #: Required information is missing or invalid; the tutor can finish it.
    INCOMPLETE = "INCOMPLETE"
    #: Every required field is filled, but an admin has not verified it yet.
    AWAITING_VERIFICATION = "AWAITING_VERIFICATION"
    #: Nothing is outstanding and the tutor has been verified.
    COMPLETE = "COMPLETE"
    #: An admin refused the profile; a new application is not accepted.
    REJECTED = "REJECTED"
    #: The account is suspended; a new application is not accepted.
    SUSPENDED = "SUSPENDED"


#: Tutor statuses that mean "the data is fine, a human has not looked yet".
#: These tutors may still apply: the admin reviews the submitted application
#: and the documents together, so blocking them would lose applicants for no
#: reason.
PENDING_VERIFICATION_STATUSES: frozenset[str] = frozenset(
    {
        "PENDING",
        "DOCUMENTS_SUBMITTED",
        "UNDER_REVIEW",
    }
)

#: Statuses that end the tutor's ability to start a new application.
BLOCKED_STATUSES: frozenset[str] = frozenset({"REJECTED", "SUSPENDED"})


@dataclass(frozen=True)
class ProfileAssessment:
    """The server's verdict on one tutor profile."""

    state: ProfileState
    percentage: int
    #: Field keys with no value, in the order the completion flow asks them.
    missing: tuple[str, ...] = ()
    #: The same fields, labelled for display.
    missing_labels: tuple[str, ...] = ()
    #: Fields holding a value that fails its own validator.
    invalid: tuple[str, ...] = ()
    #: The same fields as :attr:`invalid`, as field keys rather than labels.
    #: A caller that has to *navigate* to a field — the Mini App's job screen
    #: opening the application form on the first missing step — needs the key;
    #: the label is for people and may not match any form control's name.
    invalid_keys: tuple[str, ...] = ()
    #: Why the profile cannot be completed, for the states that refuse it.
    reason: str = ""
    #: Academic document types still outstanding for this tutor's level.
    missing_documents: tuple[str, ...] = ()

    @property
    def is_complete(self) -> bool:
        """Usable for evaluation, whether or not an admin has verified it."""
        return self.state in (ProfileState.COMPLETE, ProfileState.AWAITING_VERIFICATION)

    @property
    def is_blocked(self) -> bool:
        return self.state in (ProfileState.REJECTED, ProfileState.SUSPENDED)

    @property
    def needs_input(self) -> bool:
        return self.state == ProfileState.INCOMPLETE

    @property
    def outstanding(self) -> tuple[str, ...]:
        """Everything the tutor still has to give, invalid entries included."""
        return tuple(dict.fromkeys((*self.missing_labels, *self.invalid)))

    @property
    def outstanding_keys(self) -> tuple[str, ...]:
        """The same list as :attr:`outstanding`, as payload field keys."""
        return tuple(dict.fromkeys((*self.missing, *self.invalid_keys)))

    def summary_line(self) -> str:
        """One line describing the state, for logs and the admin view."""
        if self.state is ProfileState.NOT_STARTED:
            return "No tutor profile yet"
        base = f"{self.percentage}% complete"
        if self.missing_labels or self.invalid:
            extra = len(self.missing_labels) + len(self.invalid)
            return f"{base} — {extra} item(s) outstanding"
        return base


# ---------------------------------------------------------------------------
# conditional document requirements
# ---------------------------------------------------------------------------

#: Documents every tutor who came through the Ethiopian onboarding flow must
#: supply, whatever their level. Kept here rather than re-derived so the
#: "apply" path and the onboarding path cannot disagree.
_ALWAYS_REQUIRED: tuple[str, ...] = profile_service.REQUIRED_ONBOARDING_DOCUMENTS


def required_documents_for(tutor: Tutor) -> tuple[str, ...]:
    """The academic document types this tutor is expected to have on file.

    Conditional on purpose: an undergraduate is not asked for an MSc
    certificate and a master's graduate is not asked for a
    ``CURRENT_UNIVERSITY_DOCUMENT``. The pair the onboarding flow treats as
    mandatory (transcript and entrance result) is always included, so a tutor
    who skipped the upload step in that flow is still told what is missing here.

    Returns document *types*, not labels, so callers can compare against
    :func:`~app.services.tutor_profile_service.present_document_types`.
    """
    by_level = profile_service.academic_document_types_for(
        getattr(tutor, "education_level", None)
    )
    # A tutor with no education level yet falls back to the secondary/entrance
    # pair; the onboarding documents are still wanted on top of that.
    return tuple(dict.fromkeys((*by_level, *_ALWAYS_REQUIRED)))


def missing_required_documents(tutor: Tutor) -> tuple[str, ...]:
    """Which of :func:`required_documents_for` the tutor has not uploaded."""
    present = profile_service.present_document_types(tutor)
    return tuple(kind for kind in required_documents_for(tutor) if kind not in present)


#: Readable names for the document types, so the tutor is never shown a raw
#: enum value.
_DOCUMENT_LABELS: dict[str, str] = {
    str(DocumentType.UNIVERSITY_TRANSCRIPT): "University transcript",
    str(DocumentType.UNIVERSITY_ENTRANCE_RESULT): "University entrance exam result",
    str(DocumentType.CURRENT_UNIVERSITY_DOCUMENT): "Current university document",
    str(DocumentType.BACHELORS_DEGREE): "Bachelor's degree certificate",
    str(DocumentType.MSC_DEGREE): "Master's degree certificate",
    str(DocumentType.SECONDARY_LEAVING_RESULT): "Secondary school leaving result",
    str(DocumentType.OTHER_CERTIFICATE): "Other academic certificate",
}


def document_label(document_type: str) -> str:
    """Human name for a document type, falling back to a title-cased version."""
    return _DOCUMENT_LABELS.get(
        str(document_type), str(document_type).replace("_", " ").capitalize()
    )


# ---------------------------------------------------------------------------
# invalid values
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Check:
    """One "is this stored value usable?" test."""

    key: str
    label: str
    #: Returns True when the field is out of range or self-contradictory.
    bad: Any = field(default=lambda value: False)


def _english_out_of_range(tutor: Tutor) -> bool:
    value = tutor.english_proficiency
    if value is None:
        return False
    return not (
        settings.english_proficiency_min
        <= int(value)
        <= settings.english_proficiency_max
    )


def _age_out_of_range(tutor: Tutor) -> bool:
    value = tutor.age
    if value is None:
        return False
    return not (settings.tutor_min_age <= int(value) <= settings.tutor_max_age)


def _cgpa_out_of_range(tutor: Tutor) -> bool:
    value = tutor.cgpa
    if value is None:
        return False
    # A negative GPA is meaningless; above the configured maximum means the
    # value belongs to a different grading scale and must be re-entered.
    return float(value) < 0.0 or float(value) > float(settings.cgpa_max)


def _entrance_score_exceeds_its_own_maximum(tutor: Tutor) -> bool:
    """A score with no scale, or one above it, cannot be compared to anyone.

    This is the one academic check that is reported as invalid rather than
    missing: the tutor *did* enter a score, so re-asking the whole chain is
    wasteful, but the value on file cannot be normalised and must not be used.
    """
    score = tutor.entrance_exam_score
    maximum = tutor.entrance_exam_max_score
    if score is None or not maximum:
        return False
    return float(score) > float(maximum)


def _rejected_by_validator(tutor: Tutor, field_name: str, validator: Any) -> bool:
    """Whether the stored value for ``field_name`` fails ``validator``.

    The validators already encode the permitted values, and they all return
    ``None`` for an absent value, so calling them keeps this module from
    growing a second, drifting copy of the rules.
    """
    value = getattr(tutor, field_name, None)
    if value in (None, ""):
        return False  # missing, not invalid
    try:
        validator(value)
    except Exception:  # ProfileValidationError and anything a future one raises
        return True
    return False


def _phone_malformed(tutor: Tutor) -> bool:
    return _rejected_by_validator(tutor, "phone", profile_service.validate_phone)


def _education_level_unrecognised(tutor: Tutor) -> bool:
    """A level outside the offered set cannot be reasoned about.

    Documents are required *by* level, so an unknown level means the document
    check would be guessing. Importers can leave free text here, so it is
    checked rather than assumed.
    """
    return _rejected_by_validator(
        tutor, "education_level", profile_service.validate_education_level
    )


def _academic_stream_unrecognised(tutor: Tutor) -> bool:
    """The preparatory stream must be one the matching engine understands."""
    return _rejected_by_validator(
        tutor, "academic_stream", profile_service.validate_academic_stream
    )


_CHECKS: tuple[_Check, ...] = (
    _Check(
        "english_proficiency",
        "English proficiency (must be 1-10)",
        _english_out_of_range,
    ),
    _Check("age", "Age (must be 18 or older)", _age_out_of_range),
    _Check("cgpa", "CGPA (must be between 0 and 4)", _cgpa_out_of_range),
    _Check(
        "entrance_exam_score",
        "Entrance exam score (higher than the maximum you gave)",
        _entrance_score_exceeds_its_own_maximum,
    ),
    _Check("phone", "Phone number", _phone_malformed),
    _Check(
        "education_level",
        "Education level (choose one of the options)",
        _education_level_unrecognised,
    ),
    _Check(
        "academic_stream",
        "Preparatory stream (Natural or Social)",
        _academic_stream_unrecognised,
    ),
)


def invalid_fields(tutor: Tutor) -> list[str]:
    """Labels of the fields holding a value that fails its own validator.

    Deliberately distinct from *missing*: a missing field is answered by asking
    the question, an invalid one by asking the tutor to correct what they
    entered. English 0 and English 11 are invalid; a blank English field is
    simply absent.
    """
    return [check.label for check, bad in _invalid_checks(tutor) if bad]


def invalid_field_keys(tutor: Tutor) -> list[str]:
    """The same fields as :func:`invalid_fields`, as payload field keys."""
    return [check.key for check, bad in _invalid_checks(tutor) if bad]


def _invalid_checks(tutor: Tutor) -> list[tuple[_Check, bool]]:
    """Every check with its verdict, so both callers cannot disagree."""
    results: list[tuple[_Check, bool]] = []
    for check in _CHECKS:
        try:
            bad = bool(check.bad(tutor))
        except Exception:  # pragma: no cover - a validator must never break Apply
            logger.exception("Profile check %s raised", check.key)
            bad = False
        results.append((check, bad))
    return results


# ---------------------------------------------------------------------------
# the assessment
# ---------------------------------------------------------------------------


def assess(tutor: Tutor | None) -> ProfileAssessment:
    """Decide what has to happen before this tutor can apply.

    ``tutor`` is ``None`` when no record exists for the Telegram user, which is
    the only way to reach :attr:`ProfileState.NOT_STARTED`.
    """
    if tutor is None:
        return ProfileAssessment(state=ProfileState.NOT_STARTED, percentage=0)

    status = str(getattr(tutor, "status", "") or "").strip().upper()

    # Refused profiles are checked first: a rejected tutor who also has gaps is
    # told the rejection, not a questionnaire.
    if status in BLOCKED_STATUSES:
        return ProfileAssessment(
            state=ProfileState(
                status if status in ProfileState.__members__ else ProfileState.REJECTED
            ),
            percentage=profile_service.profile_completion_percentage(tutor),
            reason=(
                "Your tutor profile was rejected. Our team needs to review it "
                "before you can apply to a job."
                if status == "REJECTED"
                else "Your tutor account is suspended. Please contact support."
            ),
        )

    missing = tuple(profile_service.missing_profile_fields(tutor))
    missing_labels = tuple(profile_service.missing_profile_labels(tutor))
    invalid = tuple(invalid_fields(tutor))
    invalid_keys = tuple(invalid_field_keys(tutor))
    missing_docs = missing_required_documents(tutor)
    percentage = profile_service.profile_completion_percentage(tutor)

    if missing or invalid:
        return ProfileAssessment(
            state=ProfileState.INCOMPLETE,
            percentage=percentage,
            missing=missing,
            missing_labels=missing_labels,
            invalid=invalid,
            invalid_keys=invalid_keys,
            missing_documents=missing_docs,
        )

    if status in PENDING_VERIFICATION_STATUSES:
        return ProfileAssessment(
            state=ProfileState.AWAITING_VERIFICATION,
            percentage=percentage,
            missing_documents=missing_docs,
            reason=(
                "Your profile is complete and your documents are still being "
                "reviewed by our team. You can apply now."
            ),
        )

    return ProfileAssessment(state=ProfileState.COMPLETE, percentage=percentage)


def profile_is_usable(tutor: Tutor | None) -> bool:
    """Whether this profile may be evaluated for a job."""
    return assess(tutor).is_complete


def education_level_label(tutor: Tutor) -> str:
    """Readable education level, for the "what we still need" message."""
    level = str(getattr(tutor, "education_level", "") or "").strip().upper()
    labels = {
        str(EducationLevel.UNIVERSITY_STUDENT): "university student",
        str(EducationLevel.UNIVERSITY_GRADUATE): "university graduate",
        str(EducationLevel.MASTERS_STUDENT): "master's student",
        str(EducationLevel.MASTERS_GRADUATE): "master's graduate",
        str(EducationLevel.OTHER): "other",
    }
    return labels.get(level, "")


__all__ = [
    "BLOCKED_STATUSES",
    "PENDING_VERIFICATION_STATUSES",
    "ProfileAssessment",
    "ProfileState",
    "assess",
    "document_label",
    "education_level_label",
    "invalid_field_keys",
    "invalid_fields",
    "missing_required_documents",
    "profile_is_usable",
    "required_documents_for",
]