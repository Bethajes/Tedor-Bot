"""Admin keyboards (Requirements 5.1, 9.x)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

BTN_REVIEW = "🔍 Review"
BTN_VERIFY = "✅ Verify"
BTN_REJECT = "❌ Reject"
BTN_REQUEST_INFO = "📩 Request More Information"
BTN_UNDER_REVIEW = "⏳ Under Review"
BTN_SUSPEND = "⛔ Suspend"
BTN_REOPEN = "♻️ Reopen"

CB_ADMIN_MENU = "admin:menu"
CB_ADMIN_STATS = "admin:stats"
CB_ADMIN_PENDING = "admin:pending"
CB_ADMIN_SEARCH = "admin:search"
CB_ADMIN_SUPPORT = "admin:support"

#: Dashboard sections (Requirement 28).
CB_ADMIN_TUTORS = "admin:tutors"
CB_ADMIN_MATCHES = "admin:matches"
CB_ADMIN_VERIFICATION = "admin:verification"
CB_ADMIN_JOBS = "admin:jobs"

#: Starts the job-post generator conversation (owned by ``handlers.job_post``).
CB_ADMIN_POST_JOB = "admin:post_job"

#: Tutors listed on the dashboard, by status filter.
CB_TUTOR_FILTER = "admin:tutors:filter:"


def admin_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("👨‍🏫 Tutors", callback_data=CB_ADMIN_TUTORS),
                InlineKeyboardButton("🎯 Job Matches", callback_data=CB_ADMIN_MATCHES),
            ],
            [InlineKeyboardButton("⏳ Verification", callback_data=CB_ADMIN_VERIFICATION)],
            [
                InlineKeyboardButton("📊 Statistics", callback_data=CB_ADMIN_STATS),
                InlineKeyboardButton("🔎 Search Tutors", callback_data=CB_ADMIN_SEARCH),
            ],
            [InlineKeyboardButton("📰 Post Job Opening", callback_data=CB_ADMIN_POST_JOB)],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="menu:home")],
        ]
    )


def tutor_status_filter_keyboard() -> InlineKeyboardMarkup:
    """Filter the tutor list by verification status."""
    from app.enums import EthiopianTutorStatus

    statuses = (
        (EthiopianTutorStatus.PENDING, "🟡 Pending"),
        (EthiopianTutorStatus.DOCUMENTS_SUBMITTED, "📄 Documents submitted"),
        (EthiopianTutorStatus.UNDER_REVIEW, "🟠 Under review"),
        (EthiopianTutorStatus.VERIFIED, "🟢 Verified"),
        (EthiopianTutorStatus.REJECTED, "🔴 Rejected"),
        (EthiopianTutorStatus.SUSPENDED, "⚫ Suspended"),
    )
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                label, callback_data=f"{CB_TUTOR_FILTER}{status.value}"
            )
            for status, label in statuses[:3]
        ],
        [
            InlineKeyboardButton(
                label, callback_data=f"{CB_TUTOR_FILTER}{status.value}"
            )
            for status, label in statuses[3:]
        ],
        [InlineKeyboardButton("⬅️ Admin Menu", callback_data=CB_ADMIN_MENU)],
    ]
    return InlineKeyboardMarkup(rows)


def tutor_list_keyboard(tutors: Sequence[Any]) -> InlineKeyboardMarkup:
    """One row per tutor, opening the existing detail view."""
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                f"{tutor.public_tutor_id} • {tutor.display_name} ({tutor.status})",
                callback_data=f"admin:review:{tutor.public_tutor_id}",
            )
        ]
        for tutor in tutors
    ]
    rows.append(
        [
            InlineKeyboardButton("🔄 Filters", callback_data=CB_ADMIN_TUTORS),
            InlineKeyboardButton("⬅️ Admin Menu", callback_data=CB_ADMIN_MENU),
        ]
    )
    return InlineKeyboardMarkup(rows)


def job_list_keyboard(posts: Sequence[Any]) -> InlineKeyboardMarkup:
    """Open jobs, each opening its candidate list."""
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                f"{post.public_post_id} • {post.student_level} {post.subjects}",
                callback_data=f"admin:matches:{post.public_post_id}",
            )
        ]
        for post in posts
    ]
    rows.append([InlineKeyboardButton("⬅️ Admin Menu", callback_data=CB_ADMIN_MENU)])
    return InlineKeyboardMarkup(rows)


def member_referral_keyboard(tdr_id: str, current: str) -> InlineKeyboardMarkup:
    """Move the separate 50-member onboarding condition (section 7).

    Deliberately its own control on the detail view: it is an onboarding
    requirement an admin confirms, never a teaching-quality signal, so it is
    not mixed in with the verification-status buttons.
    """
    from app.enums import MemberReferralStatus

    order = (
        MemberReferralStatus.NOT_COMPLETED,
        MemberReferralStatus.PENDING_CONFIRMATION,
        MemberReferralStatus.COMPLETED,
        MemberReferralStatus.VERIFIED,
    )
    rows: list[list[InlineKeyboardButton]] = []
    current_row: list[InlineKeyboardButton] = []
    for status in order:
        mark = "✅ " if status.value == current else ""
        current_row.append(
            InlineKeyboardButton(
                f"{mark}{status.value.replace('_', ' ').title()}",
                callback_data=f"admin:referral:{status.value}:{tdr_id}",
            )
        )
        if len(current_row) == 2:
            rows.append(current_row)
            current_row = []
    if current_row:
        rows.append(current_row)
    rows.append([InlineKeyboardButton("⬅️ Admin Menu", callback_data=CB_ADMIN_MENU)])
    return InlineKeyboardMarkup(rows)


def tutor_review_keyboard(tdr_id: str) -> InlineKeyboardMarkup:
    """Buttons attached to the new-application admin notification (5.1)."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(BTN_VERIFY, callback_data=f"admin:verify:{tdr_id}"),
                InlineKeyboardButton(BTN_REJECT, callback_data=f"admin:reject:{tdr_id}"),
            ],
            [
                InlineKeyboardButton(
                    BTN_REQUEST_INFO, callback_data=f"admin:request_info:{tdr_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    BTN_UNDER_REVIEW, callback_data=f"admin:under_review:{tdr_id}"
                ),
                InlineKeyboardButton(BTN_REVIEW, callback_data=f"admin:review:{tdr_id}"),
            ],
        ]
    )


def tutor_action_keyboard(tdr_id: str, status: str = "") -> InlineKeyboardMarkup:
    """Per-tutor actions inside the pending verification list."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"{BTN_VERIFY} {tdr_id}", callback_data=f"admin:verify:{tdr_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    f"{BTN_REJECT} {tdr_id}", callback_data=f"admin:reject:{tdr_id}"
                ),
                InlineKeyboardButton(
                    BTN_REQUEST_INFO, callback_data=f"admin:request_info:{tdr_id}"
                ),
            ],
            [
                InlineKeyboardButton(
                    BTN_UNDER_REVIEW, callback_data=f"admin:under_review:{tdr_id}"
                ),
                InlineKeyboardButton(BTN_REOPEN, callback_data=f"admin:reopen:{tdr_id}"),
            ],
        ]
    )


def status_button_keyboard(tdr_id: str) -> InlineKeyboardMarkup:
    """Status buttons displayed by the ``/admin`` detail view."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🟡 PENDING", callback_data=f"admin:set:PENDING:{tdr_id}"),
                InlineKeyboardButton("🟠 UNDER_REVIEW", callback_data=f"admin:set:UNDER_REVIEW:{tdr_id}"),
            ],
            [
                InlineKeyboardButton("🟢 VERIFIED", callback_data=f"admin:set:VERIFIED:{tdr_id}"),
                InlineKeyboardButton("🔴 REJECTED", callback_data=f"admin:set:REJECTED:{tdr_id}"),
            ],
            [
                InlineKeyboardButton("⚫ SUSPENDED", callback_data=f"admin:set:SUSPENDED:{tdr_id}"),
                InlineKeyboardButton("⬅️ Admin Menu", callback_data=CB_ADMIN_MENU),
            ],
        ]
    )


def pending_tutors_keyboard(tutors: Sequence[Any]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for tutor in tutors:
        rows.append(
            [
                InlineKeyboardButton(
                    f"{tutor.public_tutor_id} • {tutor.display_name} ({tutor.status})",
                    callback_data=f"admin:review:{tutor.public_tutor_id}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton("🔄 Refresh", callback_data=CB_ADMIN_PENDING),
            InlineKeyboardButton("⬅️ Admin Menu", callback_data=CB_ADMIN_MENU),
        ]
    )
    return InlineKeyboardMarkup(rows)


def reject_reason_keyboard(tdr_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Send rejection", callback_data=f"admin:reject_confirm:{tdr_id}"
                ),
                InlineKeyboardButton("❌ Cancel", callback_data=CB_ADMIN_MENU),
            ]
        ]
    )


def verify_result_keyboard(tdr_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    BTN_REJECT, callback_data=f"admin:reject:{tdr_id}"
                ),
                InlineKeyboardButton(BTN_REQUEST_INFO, callback_data=f"admin:request_info:{tdr_id}"),
            ],
            [
                InlineKeyboardButton(BTN_SUSPEND, callback_data=f"admin:set:SUSPENDED:{tdr_id}"),
                InlineKeyboardButton("⬅️ Admin Menu", callback_data=CB_ADMIN_MENU),
            ],
        ]
    )


def search_result_keyboard(tutors: Sequence[Any]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                f"{tutor.public_tutor_id} • {tutor.display_name} ({tutor.status})",
                callback_data=f"admin:review:{tutor.public_tutor_id}",
            )
        ]
        for tutor in tutors
    ]
    rows.append(
        [
            InlineKeyboardButton("🔎 New search", callback_data=CB_ADMIN_SEARCH),
            InlineKeyboardButton("⬅️ Admin Menu", callback_data=CB_ADMIN_MENU),
        ]
    )
    return InlineKeyboardMarkup(rows)


def match_result_keyboard(post_id: str, count: int) -> InlineKeyboardMarkup:
    """Actions on the generated match report."""
    rows: list[list[InlineKeyboardButton]] = []
    if count > 0:
        rows.append(
            [InlineKeyboardButton("⭐ Shortlist a candidate", callback_data=f"match:shortlist_menu:{post_id}")]
        )
    rows.append(
        [
            InlineKeyboardButton("🔄 Re-run matching", callback_data=f"match:rerun:{post_id}"),
            InlineKeyboardButton("🏠 Admin Menu", callback_data=CB_ADMIN_MENU),
        ]
    )
    return InlineKeyboardMarkup(rows)


def candidate_actions_keyboard(job_id: str, tutor_id: str) -> InlineKeyboardMarkup:
    """Per-candidate actions after a recommendation."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("👤 View Profile", callback_data=f"match:view:{job_id}:{tutor_id}"),
                InlineKeyboardButton("📄 Documents", callback_data=f"match:docs:{job_id}:{tutor_id}"),
            ],
            [
                InlineKeyboardButton("🎤 English Recording", callback_data=f"match:voice:{job_id}:{tutor_id}"),
                InlineKeyboardButton("📩 Contact Tutor", callback_data=f"match:contact:{job_id}:{tutor_id}"),
            ],
            [
                InlineKeyboardButton("⭐ Shortlist", callback_data=f"match:shortlist:{job_id}:{tutor_id}"),
                InlineKeyboardButton("✅ Select", callback_data=f"match:select:{job_id}:{tutor_id}"),
                InlineKeyboardButton("❌ Ignore", callback_data=f"match:ignore:{job_id}:{tutor_id}"),
            ],
        ]
    )


def candidate_shortlist_keyboard(job_id: str, candidates: Sequence[Any]) -> InlineKeyboardMarkup:
    """Pick which candidate to shortlist from the report."""
    rows: list[list[InlineKeyboardButton]] = []
    for candidate in candidates:
        tutor = candidate.tutor
        rows.append(
            [
                InlineKeyboardButton(
                    f"⭐ {tutor.public_tutor_id} ({candidate.match_score:g}%)",
                    callback_data=f"match:shortlist:{job_id}:{tutor.public_tutor_id}",
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton("⬅️ Back", callback_data=f"match:back:{job_id}")]
    )
    return InlineKeyboardMarkup(rows)


def new_tutor_match_keyboard(tutor_id: str, post_id: str) -> InlineKeyboardMarkup:
    """§22: notification when a newly verified tutor strongly matches an open job."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("View Tutor", callback_data=f"admin:review:{tutor_id}"),
                InlineKeyboardButton("Shortlist", callback_data=f"match:shortlist:{post_id}:{tutor_id}"),
                InlineKeyboardButton("Ignore", callback_data=f"match:ignore:{post_id}:{tutor_id}"),
            ]
        ]
    )