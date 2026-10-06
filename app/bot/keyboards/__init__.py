"""Inline keyboard factories.

No handler module builds raw button arrays (design rule).
"""

from __future__ import annotations

from .admin import (
    admin_menu_keyboard,
    candidate_actions_keyboard,
    candidate_shortlist_keyboard,
    job_list_keyboard,
    match_result_keyboard,
    member_referral_keyboard,
    new_tutor_match_keyboard,
    pending_tutors_keyboard,
    reject_reason_keyboard,
    search_result_keyboard,
    status_button_keyboard,
    tutor_action_keyboard,
    tutor_list_keyboard,
    tutor_review_keyboard,
    tutor_status_filter_keyboard,
    verify_result_keyboard,
)
from .job_post import (
    post_choice_keyboard,
    post_edit_keyboard,
    post_nav_keyboard,
    post_preview_keyboard,
)
from .main import (
    back_to_menu_keyboard,
    help_keyboard,
    how_it_works_keyboard,
    main_menu_keyboard,
    website_button,
)
from .student import (
    no_match_keyboard,
    student_navigation_keyboard,
    student_submit_keyboard,
    tutor_card_keyboard,
    tutor_match_keyboard,
    view_tutor_keyboard,
)
from .support import support_category_keyboard, support_nav_keyboard, support_ticket_keyboard
from .tutor import (
    currency_confirm_keyboard,
    multi_select_keyboard,
    nav_keyboard,
    skip_keyboard,
    summary_keyboard,
    summary_edit_keyboard,
)

__all__ = [
    "admin_menu_keyboard",
    "back_to_menu_keyboard",
    "candidate_actions_keyboard",
    "candidate_shortlist_keyboard",
    "job_list_keyboard",
    "match_result_keyboard",
    "member_referral_keyboard",
    "new_tutor_match_keyboard",
    "reject_reason_keyboard",
    "search_result_keyboard",
    "skip_keyboard",
    "status_button_keyboard",
    "tutor_action_keyboard",
    "tutor_card_keyboard",
    "tutor_list_keyboard",
    "tutor_match_keyboard",
    "tutor_review_keyboard",
    "tutor_status_filter_keyboard",
    "student_navigation_keyboard",
    "currency_confirm_keyboard",
    "help_keyboard",
    "how_it_works_keyboard",
    "main_menu_keyboard",
    "multi_select_keyboard",
    "nav_keyboard",
    "no_match_keyboard",
    "pending_tutors_keyboard",
    "post_choice_keyboard",
    "post_edit_keyboard",
    "post_nav_keyboard",
    "post_preview_keyboard",
    "reject_reason_keyboard",
    "search_result_keyboard",
    "skip_keyboard",
    "status_button_keyboard",
    "student_navigation_keyboard",
    "student_submit_keyboard",
    "summary_edit_keyboard",
    "summary_keyboard",
    "support_category_keyboard",
    "support_nav_keyboard",
    "support_ticket_keyboard",
    "tutor_action_keyboard",
    "tutor_card_keyboard",
    "tutor_match_keyboard",
    "tutor_review_keyboard",
    "verify_result_keyboard",
    "view_tutor_keyboard",
    "website_button",
]