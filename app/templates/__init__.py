"""Static content templates.

Kept out of the conversation layer so brand copy cannot drift when flow logic
changes (see :mod:`app.templates.tutor_job_post`).
"""

from __future__ import annotations

from app.templates.tutor_job_post import (
    AMHARIC_HASHTAGS,
    BRAND_HASHTAGS,
    MAJOR_SUBJECT_ALIASES,
    MAJOR_SUBJECT_TAGS,
    REQUIRED_FIELDS,
    SUBJECT_HASHTAGS,
    SUBJECT_TAG_ORDER,
    TEMPLATE,
    TEMPLATE_PLACEHOLDERS,
    JobPostTemplateError,
    location_hashtag,
    render_job_post,
    render_subject_hashtags,
    render_tutor_category,
    template_placeholders,
    to_telegram_html,
)

__all__ = [
    "AMHARIC_HASHTAGS",
    "BRAND_HASHTAGS",
    "MAJOR_SUBJECT_ALIASES",
    "MAJOR_SUBJECT_TAGS",
    "REQUIRED_FIELDS",
    "SUBJECT_HASHTAGS",
    "SUBJECT_TAG_ORDER",
    "TEMPLATE",
    "TEMPLATE_PLACEHOLDERS",
    "JobPostTemplateError",
    "location_hashtag",
    "render_job_post",
    "render_subject_hashtags",
    "render_tutor_category",
    "template_placeholders",
    "to_telegram_html",
]