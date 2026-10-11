"""Inline keyboards for the job-application flow.

Two things live here and they behave differently.

**The button on the channel post is a URL button, not a callback button.** A
Telegram bot only receives ``callback_query`` updates for an inline button on a
channel post if the person pressing it has already started the bot; for
everyone else the button is inert and the press simply vanishes. A URL button
pointing at ``https://t.me/<bot>?start=japply_JOB-000001`` always opens the
private chat and carries the job reference with it.

**Every button the bot sends inside the private chat is a callback button** on
the ``japply:`` namespace, so continuing, going back and updating a profile are
ordinary inline callbacks.

Every piece of callback data is a fixed-length job reference of the form
``JOB-000123``. Nothing personal — no TDR id, name, phone number or document —
is ever placed in callback data or in a deep link. The job reference is the
only identifier, and the server resolves the tutor from the Telegram user id on
every press.
"""

from __future__ import annotations

from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.mini_app import web_app_button
from app.enums import JobApplicationStatus

#: Namespace for the private-chat callbacks.
CB_PREFIX = "japply:"

#: Actions. Kept as constants so the handler and the keyboards cannot drift.
CB_GO = "go"
CB_CONTINUE = "continue"
CB_BACK = "back"
CB_PROFILE = "profile"
CB_STATUS = "status"
CB_WITHDRAW = "withdraw"

#: Prefix of the ``/start`` deep-link payload a channel button points at.
#: Telegram allows 64 characters of ``[A-Za-z0-9_-]`` here; the longest payload
#: this produces is ``japply_JOB-000123`` at 17.
START_PAYLOAD_PREFIX = "japply_"

#: Every action the handler routes. Exported so a test can assert that each one
#: still fits inside Telegram's callback-data limit.
ALL_ACTIONS: tuple[str, ...] = (
    CB_GO,
    CB_CONTINUE,
    CB_BACK,
    CB_PROFILE,
    CB_STATUS,
    CB_WITHDRAW,
    "admin",
)

BTN_APPLY = "📝 Apply for This Job"
BTN_CONTINUE_APPLICATION = "✅ Continue Application"
BTN_BACK_TO_JOB = "↩️ Back to Job"
BTN_UPDATE_PROFILE = "✏️ Update My Profile"
BTN_WITHDRAW = "⏸ Withdraw Application"
#: Opens the same job inside the Mini App (Requirement 1.3). Offered in
#: parallel with the bot buttons, never instead of them.
BTN_APPLY_IN_APP = "📝 Apply in the App"

#: Telegram's hard limit on ``callback_data``.
CALLBACK_DATA_LIMIT = 64


def callback_data(action: str, job_reference: str) -> str:
    """Build one ``japply:`` callback payload.

    The result is checked against Telegram's limit here rather than at send
    time, because an over-long payload raises inside the API call where the
    cause is much harder to see.
    """
    data = f"{CB_PREFIX}{action}:{job_reference}".strip()
    if len(data.encode("utf-8")) > CALLBACK_DATA_LIMIT:  # pragma: no cover - defensive
        raise ValueError(f"callback_data too long for Telegram: {data!r}")
    return data


def deep_link(username: str, job_reference: str) -> str:
    """The ``t.me`` URL the channel button opens.

    ``username`` may arrive with or without a leading ``@``. The job reference
    is the only thing carried — no user data rides in the link.
    """
    handle = str(username or "").strip().lstrip("@")
    if not handle:
        raise ValueError("A bot username is required to build the apply deep link")
    return f"https://t.me/{handle}?start={START_PAYLOAD_PREFIX}{job_reference}"


def parse_start_payload(payload: str) -> str | None:
    """The job reference carried by a ``/start`` payload, or ``None``.

    Anything that is not an apply payload is ignored so the ordinary ``/start``
    with no arguments — and any future payload — is unaffected.
    """
    text = str(payload or "").strip()
    if not text.startswith(START_PAYLOAD_PREFIX):
        return None
    return text[len(START_PAYLOAD_PREFIX) :] or None


def parse_callback_data(data: str) -> tuple[str, str] | None:
    """Split ``japply:<action>:<job>`` into its parts, or ``None`` if foreign."""
    text = str(data or "")
    if not text.startswith(CB_PREFIX):
        return None
    parts = text.split(":")
    if len(parts) < 3:
        return None
    return parts[1], parts[2]


def job_apply_keyboard(job_reference: str, username: str) -> InlineKeyboardMarkup:
    """The single button attached beneath a published job post.

    A URL button, deliberately — see the module docstring. When no username is
    configured the caller falls back to a callback button, which still works
    for anyone who has already started the bot.
    """
    if username:
        return InlineKeyboardMarkup(
            [[InlineKeyboardButton(BTN_APPLY, url=deep_link(username, job_reference))]]
        )
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    BTN_APPLY, callback_data=callback_data(CB_GO, job_reference)
                )
            ]
        ]
    )


def apply_intro_keyboard(job_reference: str, username: str = "") -> InlineKeyboardMarkup:
    """Shown when a press arrived from the channel and must continue privately."""
    rows: list[list[InlineKeyboardButton]] = []
    if username:
        rows.append(
            [
                InlineKeyboardButton(
                    BTN_CONTINUE_APPLICATION, url=deep_link(username, job_reference)
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton(BTN_BACK_TO_JOB, callback_data=callback_data(CB_BACK, job_reference))]
    )
    # The Mini App opens the same job screen, carrying the reference in the
    # hash (Requirements 1.3, 1.4). Skipped entirely when unconfigured.
    mini_app = web_app_button(BTN_APPLY_IN_APP, "job", job_reference)
    if mini_app is not None:
        rows.append([mini_app])
    return InlineKeyboardMarkup(rows)


def recommendation_keyboard(job_reference: str) -> InlineKeyboardMarkup:
    """The three choices shown with a personalised recommendation.

    Offered for both strong and weaker matches: a low score is advice, and the
    tutor decides whether to proceed.
    """
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                BTN_CONTINUE_APPLICATION,
                callback_data=callback_data(CB_CONTINUE, job_reference),
            )
        ],
        [
            InlineKeyboardButton(
                BTN_BACK_TO_JOB, callback_data=callback_data(CB_BACK, job_reference)
            ),
            InlineKeyboardButton(
                BTN_UPDATE_PROFILE,
                callback_data=callback_data(CB_PROFILE, job_reference),
            ),
        ],
    ]
    # Same parallel Mini App button as the intro keyboard (Requirements 1.3).
    mini_app = web_app_button(BTN_APPLY_IN_APP, "job", job_reference)
    if mini_app is not None:
        rows.append([mini_app])
    return InlineKeyboardMarkup(rows)


def existing_application_keyboard(
    job_reference: str, status: str, *, confirmed: bool
) -> InlineKeyboardMarkup:
    """What a tutor who has already applied is offered instead.

    A submitted application shows its status and offers only a withdrawal — no
    second submit, because the database row is already final and re-sending it
    would only notify the admin twice.
    """
    rows: list[list[InlineKeyboardButton]] = []
    if not confirmed:
        rows.append(
            [
                InlineKeyboardButton(
                    BTN_CONTINUE_APPLICATION,
                    callback_data=callback_data(CB_CONTINUE, job_reference),
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                BTN_UPDATE_PROFILE,
                callback_data=callback_data(CB_PROFILE, job_reference),
            )
        ]
    )
    if status in {
        str(JobApplicationStatus.STARTED),
        str(JobApplicationStatus.READY_TO_SUBMIT),
        str(JobApplicationStatus.SUBMITTED),
        str(JobApplicationStatus.UNDER_REVIEW),
    }:
        rows.append(
            [
                InlineKeyboardButton(
                    BTN_WITHDRAW,
                    callback_data=callback_data(CB_WITHDRAW, job_reference),
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton(BTN_BACK_TO_JOB, callback_data=callback_data(CB_BACK, job_reference))]
    )
    return InlineKeyboardMarkup(rows)


def job_application_admin_keyboard(job_reference: str) -> InlineKeyboardMarkup:
    """Shown with the interest notification, fired before anything is submitted.

    Only one button, because it can only do one useful thing yet: open the
    job's candidate list. The tutor has not applied, so there is no application
    row to shortlist.
    """
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🎯 View Job Matches", callback_data=f"admin:matches:{job_reference}"
                )
            ]
        ]
    )


def application_list_keyboard(applications_list: Any) -> InlineKeyboardMarkup:
    """One row per application, each opening its detail view."""
    rows: list[list[InlineKeyboardButton]] = []
    for application in applications_list:
        rows.append(
            [
                InlineKeyboardButton(
                    f"{application.job_id} • {application.public_tutor_id} "
                    f"({application.match_score:g}%)",
                    callback_data=f"admin:app:{application.id}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton("🔄 Refresh", callback_data="admin:applications"),
            InlineKeyboardButton("⬅️ Admin Menu", callback_data="admin:menu"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def application_filter_keyboard(current: str = "") -> InlineKeyboardMarkup:
    """Filter the application list by status."""
    order = (
        (JobApplicationStatus.SUBMITTED, "📩 Submitted"),
        (JobApplicationStatus.UNDER_REVIEW, "⏳ Under Review"),
        (JobApplicationStatus.SHORTLISTED, "⭐ Shortlisted"),
        (JobApplicationStatus.REJECTED, "❌ Rejected"),
        (JobApplicationStatus.WITHDRAWN, "⏸ Withdrawn"),
    )
    rows: list[list[InlineKeyboardButton]] = []
    current_row: list[InlineKeyboardButton] = []
    for status, label in order:
        mark = "✅ " if status.value == current else ""
        current_row.append(
            InlineKeyboardButton(
                f"{mark}{label}", callback_data=f"admin:app_filter:{status.value}"
            )
        )
        if len(current_row) == 2:
            rows.append(current_row)
            current_row = []
    if current_row:
        rows.append(current_row)
    rows.append(
        [
            InlineKeyboardButton("All", callback_data="admin:app_filter:"),
            InlineKeyboardButton("⬅️ Admin Menu", callback_data="admin:menu"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def admin_application_keyboard(
    application_id: int, job_reference: str, tdr_id: str, status: str
) -> InlineKeyboardMarkup:
    """Admin actions on one submitted application.

    Reuses the existing admin namespaces for viewing a tutor profile and the
    candidate match view, so nothing here duplicates a review screen.

    Takes plain values rather than the ORM object, so a notification can build
    its keyboard after its session has closed.
    """
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton("👤 View Profile", callback_data=f"admin:review:{tdr_id}"),
            InlineKeyboardButton(
                "🎯 Match View", callback_data=f"match:view:{job_reference}:{tdr_id}"
            ),
        ]
    ]
    moves: list[tuple[str, str]] = []
    if status != str(JobApplicationStatus.UNDER_REVIEW):
        moves.append(("⏳ Under Review", f"japply:admin:review:{application_id}"))
    if status != str(JobApplicationStatus.SHORTLISTED):
        moves.append(("⭐ Shortlist", f"japply:admin:shortlist:{application_id}"))
    if status != str(JobApplicationStatus.REJECTED):
        moves.append(("❌ Reject", f"japply:admin:reject:{application_id}"))
    for index in range(0, len(moves), 2):
        rows.append(
            [
                InlineKeyboardButton(label, callback_data=data)
                for label, data in moves[index : index + 2]
            ]
        )
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="admin:applications")])
    return InlineKeyboardMarkup(rows)


__all__ = [
    "ALL_ACTIONS",
    "BTN_APPLY",
    "BTN_BACK_TO_JOB",
    "BTN_CONTINUE_APPLICATION",
    "BTN_UPDATE_PROFILE",
    "BTN_WITHDRAW",
    "CB_BACK",
    "CB_CONTINUE",
    "CB_GO",
    "CB_PREFIX",
    "CB_PROFILE",
    "CB_STATUS",
    "CB_WITHDRAW",
    "CALLBACK_DATA_LIMIT",
    "START_PAYLOAD_PREFIX",
    "admin_application_keyboard",
    "application_filter_keyboard",
    "application_list_keyboard",
    "apply_intro_keyboard",
    "callback_data",
    "deep_link",
    "existing_application_keyboard",
    "job_apply_keyboard",
    "job_application_admin_keyboard",
    "parse_callback_data",
    "parse_start_payload",
    "recommendation_keyboard",
]