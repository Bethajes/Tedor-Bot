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

#: Applications tutors submitted through the Apply button on a job post.
CB_ADMIN_APPLICATIONS = "admin:applications"

#: Starts the job-post generator conversation (owned by ``handlers.job_post``).
CB_ADMIN_POST_JOB = "admin:post_job"

#: Tutors listed on the dashboard, by status filter.
CB_TUTOR_FILTER = "admin:tutors:filter:"

#: Permanent removal of a tutor. Split into two steps so a mis-tap on the
#: dashboard cannot destroy an application: the first only asks, the second
#: carries the reference and does it.
CB_ADMIN_DELETE = "admin:delete:"
CB_ADMIN_DELETE_CONFIRM = "admin:delete_confirm:"


def admin_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("👨‍🏫 Tutors", callback_data=CB_ADMIN_TUTORS),
                InlineKeyboardButton("🎯 Job Matches", callback_data=CB_ADMIN_MATCHES),
            ],
            [InlineKeyboardButton("📥 Job Applications", callback_data=CB_ADMIN_APPLICATIONS)],
            [InlineKeyboardButton("⏳ Verification", callback_data=CB_ADMIN_VERIFICATION)],
            [
                InlineKeyboardButton("📊 Statistics", callback_data=CB_ADMIN_STATS),
                InlineKeyboardButton("🔎 Search Tutors", callback_data=CB_ADMIN_SEARCH),
            ],
            [InlineKeyboardButton("📍 Locations", callback_data=CB_ADMIN_LOCATIONS)],
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
            [
                InlineKeyboardButton(
                    "🗑 Delete tutor", callback_data=f"{CB_ADMIN_DELETE}{tdr_id}"
                )
            ],
        ]
    )


def delete_confirm_keyboard(tdr_id: str, display_name: str) -> InlineKeyboardMarkup:
    """The irreversible step of a tutor deletion.

    The confirm button names the tutor, so the admin can see who they are about
    to destroy without going back to the detail view.
    """
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"🗑 Yes, delete {display_name}", callback_data=f"{CB_ADMIN_DELETE_CONFIRM}{tdr_id}"
                )
            ],
            [InlineKeyboardButton("↩️ Cancel", callback_data=f"admin:review:{tdr_id}")],
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

# ---------------------------------------------------------------------------
# Location review (specification section 6)
# ---------------------------------------------------------------------------

#: A button that acknowledges without doing anything, used for labels the admin
#: needs to read but must not press.
CB_LOC_NOOP = "loc:noop"

#: The pending-locations queue from the admin menu.
CB_ADMIN_LOCATIONS = "admin:locations"

#: One page of the queue. The page number is in the callback rather than the
#: label, so a long queue costs no extra bytes.
CB_LOC_PAGE = "admin:loc:page:"

#: One location's detail screen and its actions.
CB_LOC_DETAIL = "admin:loc:detail:"
CB_LOC_APPROVE = "admin:loc:approve:"
CB_LOC_REJECT = "admin:loc:reject:"
CB_LOC_REJECT_CONFIRM = "admin:loc:rejectconfirm:"
CB_LOC_MERGE = "admin:loc:merge:"
CB_LOC_MERGE_DO = "admin:loc:mergeinto:"
CB_LOC_ALIAS = "admin:loc:alias:"
CB_LOC_COORDS = "admin:loc:coords:"

#: Per page in the review queue.
LOCATIONS_PER_PAGE = 8

#: One tutor row whose text has not been matched to a location.
CB_LOC_LINK = "admin:loc:link:"

#: The work list of tutor rows still pointing at nothing.
CB_LOC_UNLINKED = "admin:loc:unlinked"


def locations_menu_keyboard() -> InlineKeyboardMarkup:
    """The location review entry point, with the pending count when known."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("⏳ Review new locations", callback_data=CB_ADMIN_LOCATIONS)],
            [InlineKeyboardButton("⚠️ Unmatched tutor areas", callback_data=CB_LOC_UNLINKED)],
            [InlineKeyboardButton("🔎 Find a location", callback_data="admin:loc:find")],
            [InlineKeyboardButton("⬅️ Admin Menu", callback_data=CB_ADMIN_MENU)],
        ]
    )


def location_list_keyboard(
    locations: Sequence[Any],
    *,
    page: int,
    total_pages: int,
    pending: int = 0,
) -> InlineKeyboardMarkup:
    """A page of the review queue: one button per location, plus paging."""
    rows: list[list[InlineKeyboardButton]] = []
    if pending:
        rows.append([InlineKeyboardButton(f"⏳ {pending} awaiting review", callback_data=CB_LOC_NOOP)])
    for location in locations:
        badge = "" if location.is_verified else " ⚠️"
        rows.append(
            [
                InlineKeyboardButton(
                    f"📍 {location.name}{badge}",
                    callback_data=f"{CB_LOC_DETAIL}{location.id}",
                )
            ]
        )
    paging: list[InlineKeyboardButton] = []
    if page > 0:
        paging.append(
            InlineKeyboardButton("⬅️ Prev", callback_data=f"{CB_LOC_PAGE}{page - 1}")
        )
    if page < total_pages - 1:
        paging.append(
            InlineKeyboardButton("Next ➡️", callback_data=f"{CB_LOC_PAGE}{page + 1}")
        )
    if paging:
        rows.append(paging)
    rows.append([InlineKeyboardButton("⬅️ Admin Menu", callback_data=CB_ADMIN_MENU)])
    return InlineKeyboardMarkup(rows)


def location_detail_keyboard(location_id: int) -> InlineKeyboardMarkup:
    """The review actions for one location."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"{CB_LOC_APPROVE}{location_id}"),
            ],
            [
                InlineKeyboardButton("❌ Reject", callback_data=f"{CB_LOC_REJECT}{location_id}"),
            ],
            [
                InlineKeyboardButton("🔀 Merge into another", callback_data=f"{CB_LOC_MERGE}{location_id}"),
            ],
            [
                InlineKeyboardButton("🔤 Add spelling", callback_data=f"{CB_LOC_ALIAS}{location_id}"),
                InlineKeyboardButton("📍 Set coordinates", callback_data=f"{CB_LOC_COORDS}{location_id}"),
            ],
            [
                InlineKeyboardButton("⬅️ Back to list", callback_data=CB_ADMIN_LOCATIONS),
            ],
        ]
    )


def location_reject_reason_keyboard(location_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Reject anyway", callback_data=f"{CB_LOC_REJECT_CONFIRM}{location_id}"
                ),
                InlineKeyboardButton("✖️ Keep it", callback_data=f"{CB_LOC_DETAIL}{location_id}"),
            ],
        ]
    )


def location_merge_keyboard(
    source: Any, targets: Sequence[Any]
) -> InlineKeyboardMarkup:
    """Candidate parents for a merge, with a way back out.

    The source is shown as a disabled-looking first row rather than hidden, so
    an admin can see what they are merging *from* while they choose *into*.
    """
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(f"📤 From: {source.name}", callback_data=CB_LOC_NOOP)]
    ]
    for target in targets:
        rows.append(
            [
                InlineKeyboardButton(
                    f"📍 {target.name}",
                    callback_data=f"{CB_LOC_MERGE_DO}{source.id}:{target.id}",
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton("✖️ Cancel", callback_data=f"{CB_LOC_DETAIL}{source.id}")]
    )
    return InlineKeyboardMarkup(rows)


def location_result_keyboard(location_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📍 Open location", callback_data=f"{CB_LOC_DETAIL}{location_id}"),
            ],
            [InlineKeyboardButton("⬅️ Admin Menu", callback_data=CB_ADMIN_MENU)],
        ]
    )


def location_unlinked_keyboard(
    rows: Sequence[Any], *, limit: int = 100
) -> InlineKeyboardMarkup:
    """Tutor location rows that still point at nothing.

    Each button opens the location it should probably become, so an admin links
    rather than guesses: a row whose text matches nothing in our records stays
    unlinked, and the tutor keeps exactly what they typed.
    """
    rows_out: list[list[InlineKeyboardButton]] = []
    for row in rows[:limit]:
        rows_out.append(
            [
                InlineKeyboardButton(
                    f"\u26a0\ufe0f {row.location}",
                    callback_data=f"{CB_LOC_LINK}{row.id}",
                )
            ]
        )
    rows_out.append(
        [InlineKeyboardButton("\u2b05\ufe0f Admin Menu", callback_data=CB_ADMIN_MENU)]
    )
    return InlineKeyboardMarkup(rows_out)
