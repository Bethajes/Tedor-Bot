"""Static Tedor Tutors job-post template.

The Requirements block is *part of this file*, not a placeholder. There is
deliberately no ``{requirements}`` variable, so the wording cannot be changed by
the admin, by the conversation, or by any downstream parser — the only value
that varies inside that block is ``{location}``, which is the same value already
used in the main description.

Placeholder set is asserted by the test suite against
:data:`TEMPLATE_PLACEHOLDERS` so adding a variable is a deliberate act.
"""

from __future__ import annotations

import html
import re
import string
import unicodedata

__all__ = [
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


class JobPostTemplateError(ValueError):
    """Raised when the template cannot be rendered from the given values."""


# ---------------------------------------------------------------------------
# The template
# ---------------------------------------------------------------------------

#: Fields the conversation collects. The requirements block is not one of them.
REQUIRED_FIELDS: tuple[str, ...] = (
    "location",
    "student_level",
    "tutor_category",
    "duration",
    "frequency",
    "subjects",
    "compensation",
)

TEMPLATE = """🚨 **Urgent Job Opening** 📰

Job Status: **Open**

📃 **Tedor Tutors** is currently seeking an excellent, punctual, and dedicated tutor with strong English fluency for a tutoring opportunity in #{location}.

✍️ **Student's Level**: {student_level}

✍️ **Tutor Category**: {tutor_category}

✍️ **Duration**: {duration}

✍️ **Frequency**: {frequency}

✍️ **Target Subjects**: {subjects}

💰 **Compensation**: {compensation}

✅ **Requirements**:

☑️ Proximity to #{location},
☑️ Solid #academic background,
☑️ Passion for #teaching,
☑️ Strong command of the #English language and robust #Math foundation.

📩 Contact us: @Tedor_Team

#TEDOR #TUTORING #TUTORIAL #TUTOR {subject_hashtags} #አስጠኚዎች #አስጠኚ"""

#: Brand hashtags appended to every post, in order.
BRAND_HASHTAGS: tuple[str, ...] = (
    "#TEDOR",
    "#TUTORING",
    "#TUTORIAL",
    "#TUTOR",
)

#: Amharic hashtags appended to every post, in order.
AMHARIC_HASHTAGS: tuple[str, ...] = ("#አስጠኚዎች", "#አስጠኚ")


def template_placeholders(template: str = TEMPLATE) -> tuple[str, ...]:
    """Every ``{placeholder}`` the template accepts, in order of appearance."""
    names: list[str] = []
    for _literal, field, _spec, _conv in string.Formatter().parse(template):
        if field and field not in names:
            names.append(field)
    return tuple(names)


#: {subject_hashtags} is derived, not collected.
TEMPLATE_PLACEHOLDERS: tuple[str, ...] = template_placeholders()


# ---------------------------------------------------------------------------
# Location -> hashtag
# ---------------------------------------------------------------------------

_NON_TAG_CHARS = re.compile(r"[^\w]+", re.UNICODE)


def location_hashtag(location: str) -> str:
    """``Ayat Tsebel`` -> ``Ayat_Tsebel``.

    The admin types the plain place name once; this turns it into the
    Telegram-friendly tag reused in both the description and the requirements.
    Meaningful capitalisation is preserved (``Bole`` stays ``Bole``, ``CMC``
    stays ``CMC``). Any leading ``#`` the admin typed is dropped, and runs of
    non-word characters become a single underscore.
    """
    text = unicodedata.normalize("NFKC", str(location or "")).strip()
    text = text.lstrip("#").strip()
    return _NON_TAG_CHARS.sub("_", text).strip("_")


# ---------------------------------------------------------------------------
# Tutor category -> hashtags
# ---------------------------------------------------------------------------

_CATEGORY_TAG_WORDS = frozenset({"male", "female"})
_CATEGORY_WORD_RE = re.compile(r"\b(male|female)\b", re.IGNORECASE)


def render_tutor_category(category: str) -> str:
    """``Both Male and Female`` -> ``Both #Male and #Female``.

    Only the two gender words become hashtags; every other word is left exactly
    as the admin typed it.
    """
    text = " ".join(str(category or "").split())
    if not text:
        return ""

    def _replace(match: re.Match[str]) -> str:
        word = match.group(0)
        if word.casefold() not in _CATEGORY_TAG_WORDS:  # pragma: no cover - regex
            return word
        return f"#{word[0].upper()}{word[1:].lower()}"

    return _CATEGORY_WORD_RE.sub(_replace, text)


# ---------------------------------------------------------------------------
# Subjects -> hashtags
# ---------------------------------------------------------------------------

#: Canonical subject -> tag. Keys are compared case-folded.
SUBJECT_HASHTAGS: dict[str, str] = {
    "english": "ENGLISH",
    "amharic": "AMHARIC",
    "mathematics": "MATH",
    "math": "MATH",
    "maths": "MATH",
    "science": "SCIENCE",
    "physics": "PHYSICS",
    "chemistry": "CHEMISTRY",
    "biology": "BIOLOGY",
    "geography": "GEOGRAPHY",
    "history": "HISTORY",
    "economics": "ECONOMICS",
    "computer science": "COMPUTER_SCIENCE",
    "ict": "ICT",
    "programming": "PROGRAMMING",
    "accounting": "ACCOUNTING",
    "business": "BUSINESS",
    "creative arts": "CREATIVE_ARTS",
    "music": "MUSIC",
    "sports": "SPORTS",
    "french": "FRENCH",
    "arabic": "ARABIC",
}

#: Deterministic emission order, so the same subjects always yield the same tags.
SUBJECT_TAG_ORDER: tuple[str, ...] = (
    "ENGLISH",
    "MATH",
    "SCIENCE",
    "PHYSICS",
    "CHEMISTRY",
    "BIOLOGY",
    "GEOGRAPHY",
    "HISTORY",
    "ECONOMICS",
    "COMPUTER_SCIENCE",
    "AMHARIC",
    "PROGRAMMING",
    "ICT",
    "ACCOUNTING",
    "BUSINESS",
    "CREATIVE_ARTS",
    "MUSIC",
    "SPORTS",
    "FRENCH",
    "ARABIC",
)

#: "Major Subjects" is the one bundled subject the spec pins explicitly.
MAJOR_SUBJECT_TAGS: tuple[str, ...] = ("ENGLISH", "MATH", "SCIENCE")

#: Substrings that mean "Major Subjects".
MAJOR_SUBJECT_ALIASES: tuple[str, ...] = ("major subject", "major")

_SUBJECT_SPLIT_RE = re.compile(r"[,/\n+&]| and ", re.IGNORECASE)
_NON_UPPER_TAG_RE = re.compile(r"[^\w]+", re.UNICODE)


def _upper_tag(value: str) -> str:
    """Fallback tag for a subject with no explicit mapping."""
    cleaned = _NON_UPPER_TAG_RE.sub("_", str(value or "").strip().upper()).strip("_")
    return cleaned


def _order_tags(tags: list[str]) -> list[str]:
    """Known tags first in canonical order, then the rest alphabetically."""
    unique = list(dict.fromkeys(tag for tag in tags if tag))
    known = [tag for tag in SUBJECT_TAG_ORDER if tag in unique]
    extra = sorted(tag for tag in unique if tag not in SUBJECT_TAG_ORDER)
    return known + extra


def render_subject_hashtags(subjects: str) -> str:
    """``Major Subjects`` -> ``#ENGLISH #MATH #SCIENCE``.

    Returns the tags with their leading ``#`` already attached, ready to drop
    into the template's hashtag line. Irrelevant tags are never invented: an
    unmapped subject only becomes a tag derived from its own name.
    """
    parts = [
        part.strip()
        for part in _SUBJECT_SPLIT_RE.split(str(subjects or ""))
        if part.strip()
    ]

    tags: list[str] = []
    for part in parts:
        key = part.casefold()
        if any(alias in key for alias in MAJOR_SUBJECT_ALIASES):
            tags.extend(MAJOR_SUBJECT_TAGS)
            continue
        tags.append(SUBJECT_HASHTAGS.get(key) or _upper_tag(part))

    return " ".join(f"#{tag}" for tag in _order_tags(tags))


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _clean(value: object) -> str:
    return " ".join(str(value if value is not None else "").split())


def render_job_post(
    *,
    location: str,
    student_level: str,
    tutor_category: str,
    duration: str,
    frequency: str,
    subjects: str,
    compensation: str,
) -> str:
    """Fill the static template with the seven collected values.

    ``location`` is converted to its hashtag form here, which is what makes the
    same value appear in both the description and the requirements block.
    """
    values = {
        "location": location_hashtag(location),
        "student_level": _clean(student_level),
        "tutor_category": render_tutor_category(tutor_category),
        "duration": _clean(duration),
        "frequency": _clean(frequency),
        "subjects": _clean(subjects),
        "compensation": _clean(compensation),
        "subject_hashtags": render_subject_hashtags(subjects),
    }

    empty = [
        name
        for name in REQUIRED_FIELDS
        if not values[name]  # type: ignore[literal-required]
    ]
    if empty:
        raise JobPostTemplateError(
            "Cannot render the job post — missing: " + ", ".join(empty)
        )

    return TEMPLATE.format(**values)


_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)


def to_telegram_html(text: str) -> str:
    """Convert the canonical post text into Telegram HTML parse-mode text.

    The stored/generated form keeps the ``**bold**`` markers the brand voice is
    written in; Telegram needs ``<b>`` instead, so admin-entered values are
    escaped first and only then wrapped.
    """
    escaped = html.escape(str(text or ""), quote=False)
    return _BOLD_RE.sub(r"<b>\1</b>", escaped)