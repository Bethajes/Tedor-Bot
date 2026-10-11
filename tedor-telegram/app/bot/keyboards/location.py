"""Keyboards for the location selector.

The selector is the same three screens wherever it is used — search, results,
manual entry — so the keyboards live here rather than in each flow's module.

Callback data is deliberately tiny. Telegram caps ``callback_data`` at 64
bytes, and Amharic neighbourhood names are three bytes per character, so a
result is never addressed by its name. A suggestion is referenced by its
index into a short-lived list the handler keeps in ``context.user_data``, which
keeps every button here under a dozen bytes and leaves room for the next
bot owner to extend the namespaces.
"""

from __future__ import annotations

from collections.abc import Sequence

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.services.geocoding.base import PlaceSuggestion

# ---------------------------------------------------------------------------
# Namespaces
# ---------------------------------------------------------------------------

#: Chosen so all four prefixes share a lengthiest-addressable shape.
CB_SEARCH = "loc:search"
CB_MANUAL = "loc:manual"
CB_CHOOSE = "loc:choose:"
CB_REMOVE = "loc:remove:"
CB_MORE = "loc:more:"
CB_DONE = "loc:done"
CB_BACK = "loc:back"
CB_CANCEL = "loc:cancel"

#: The manual-entry flow, shared by the tutor and parent selectors.
CB_MANUAL_NAME = "loc:man:name"
CB_MANUAL_CITY = "loc:man:city"
CB_MANUAL_CITY_OTHER = "loc:man:cityother"
CB_MANUAL_SUB = "loc:man:sub"
CB_MANUAL_SKIP_SUB = "loc:man:subskip"
CB_MANUAL_LANDMARK = "loc:man:landmark"
CB_MANUAL_SKIP_LANDMARK = "loc:man:landmarkskip"
CB_MANUAL_SAVE = "loc:man:save"

#: Confirm-or-merge when a near-duplicate already exists.
CB_KEEP = "loc:keep"
CB_RESTART = "loc:restart"

#: The home-area question asked after a tutor answers with their address.
CB_HOME_YES = "eth:home:yes"
CB_HOME_NO = "eth:home:no"


def suggestion_rows(
    suggestions: Sequence[PlaceSuggestion],
    *,
    selected: Sequence[str] = (),
    page: int = 0,
    page_size: int = 5,
) -> list[list[InlineKeyboardButton]]:
    """Result buttons, one per suggestion, plus a paging row when needed.

    ``page`` is the index of the first result to show. Each button carries only
    its index in the full list, so paging costs no callback bytes and a stale
    button resolves to a result the handler can reject by telling the user to
    search again.
    """
    rows: list[list[InlineKeyboardButton]] = []
    start = max(0, page)
    page_size = max(1, page_size)
    window = suggestions[start : start + page_size]

    for position, suggestion in enumerate(window):
        rows.append(
            [
                InlineKeyboardButton(
                    _button_label(suggestion),
                    callback_data=f"{CB_CHOOSE}{start + position}",
                )
            ]
        )

    paging: list[InlineKeyboardButton] = []
    if start > 0:
        paging.append(
            InlineKeyboardButton(
                "⬅️ Previous", callback_data=f"{CB_MORE}{max(0, start - page_size)}"
            )
        )
    if start + page_size < len(suggestions):
        paging.append(
            InlineKeyboardButton(
                "More ➡️", callback_data=f"{CB_MORE}{start + page_size}"
            )
        )
    if paging:
        rows.append(paging)
    return rows


def _button_label(suggestion: PlaceSuggestion) -> str:
    """A button label that fits one line on a phone screen.

    Telegram truncates long labels mid-word, so the sub-city is dropped rather
    than the place name when there is no room, and the city alone is never
    repeated from every row.
    """
    name = suggestion.name.strip()
    if len(name) <= 26:
        return name
    return name[:25].rstrip() + "…"


def action_rows(*, show_manual: bool = True, show_done: bool = True) -> list[list[InlineKeyboardButton]]:
    """The fallback and dismissal rows shown under results."""
    rows: list[list[InlineKeyboardButton]] = []
    if show_manual:
        rows.append(
            [
                InlineKeyboardButton(
                    "❓ Can't find your location? Add it manually.",
                    callback_data=CB_MANUAL,
                )
            ]
        )
    if show_done:
        rows.append([InlineKeyboardButton("✅ Done", callback_data=CB_DONE)])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data=CB_BACK)])
    return rows


def results_keyboard(
    suggestions: Sequence[PlaceSuggestion],
    *,
    selected: Sequence[str] = (),
    page: int = 0,
    show_manual: bool = True,
    show_done: bool = True,
) -> InlineKeyboardMarkup:
    """The full results screen: suggestions, their navigation and the fallback."""
    rows = suggestion_rows(suggestions, selected=selected, page=page)
    rows.extend(action_rows(show_manual=show_manual, show_done=show_done))
    return InlineKeyboardMarkup(rows)


def chip_rows(selected: Sequence[dict[str, str]]) -> list[list[InlineKeyboardButton]]:
    """One row per already-picked location, each with a remove button.

    The callback carries the chip's index, never its text, for the same reason
    a suggestion button does not.
    """
    rows: list[list[InlineKeyboardButton]] = []
    for index, entry in enumerate(selected):
        label = entry.get("name") or entry.get("location") or "Location"
        rows.append(
            [
                InlineKeyboardButton(
                    f"📍 {_chip_label(label)}",
                    callback_data="loc:noop",
                ),
                InlineKeyboardButton("❌", callback_data=f"{CB_REMOVE}{index}"),
            ]
        )
    return rows


def _chip_label(label: str) -> str:
    if len(label) <= 28:
        return label
    return label[:27].rstrip() + "…"


def search_keyboard() -> InlineKeyboardMarkup:
    """The screen shown before anything is typed: fallback first, always reachable."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "❓ Can't find your location? Add it manually.",
                    callback_data=CB_MANUAL,
                )
            ],
            [InlineKeyboardButton("⬅️ Back", callback_data=CB_BACK)],
        ]
    )


def empty_keyboard(*, show_done: bool = True) -> InlineKeyboardMarkup:
    """No results: the manual fallback is the whole point of this screen."""
    return InlineKeyboardMarkup(action_rows(show_done=show_done))


def error_keyboard() -> InlineKeyboardMarkup:
    """A provider error: fallback first, then a retry."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "❓ Can't find your location? Add it manually.",
                    callback_data=CB_MANUAL,
                )
            ],
            [
                InlineKeyboardButton("🔄 Search again", callback_data=CB_SEARCH),
            ],
            [InlineKeyboardButton("⬅️ Back", callback_data=CB_BACK)],
        ]
    )


def home_area_keyboard() -> InlineKeyboardMarkup:
    """The home-area question: asked once, answered explicitly."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Yes, I can tutor there", callback_data=CB_HOME_YES),
            ],
            [
                InlineKeyboardButton("➡️ No, let me pick", callback_data=CB_HOME_NO),
            ],
        ]
    )


# ---------------------------------------------------------------------------
# Manual entry
# ---------------------------------------------------------------------------


def manual_name_keyboard() -> InlineKeyboardMarkup:
    """The first manual screen, with the typed text already pre-filled above it."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "❌ Cancel", callback_data=CB_CANCEL,
                )
            ]
        ]
    )


def city_keyboard(cities: Sequence[str] = ()) -> InlineKeyboardMarkup:
    """City choices, Addis Ababa first.

    The default is offered as a button rather than assumed, so a city outside
    the list stays reachable through the "Other" path.
    """
    rows: list[list[InlineKeyboardButton]] = []
    current: list[InlineKeyboardButton] = []
    for index, city in enumerate(cities):
        current.append(
            InlineKeyboardButton(city, callback_data=f"loc:city:{index}")
        )
        if len(current) == 2:
            rows.append(current)
            current = []
    if current:
        rows.append(current)
    rows.append(
        [InlineKeyboardButton("🌍 Other city…", callback_data=CB_MANUAL_CITY_OTHER)]
    )
    rows.append(
        [
            InlineKeyboardButton("⬅️ Back", callback_data=CB_BACK),
            InlineKeyboardButton("❌ Cancel", callback_data=CB_CANCEL),
        ]
    )
    return InlineKeyboardMarkup(rows)


def optional_keyboard(
    skip_callback: str, *, skip_label: str = "⏭ Skip this"
) -> InlineKeyboardMarkup:
    """A keyboard for an optional manual field: skip, back, cancel."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(skip_label, callback_data=skip_callback),
            ],
            [
                InlineKeyboardButton("⬅️ Back", callback_data=CB_BACK),
                InlineKeyboardButton("❌ Cancel", callback_data=CB_CANCEL),
            ],
        ]
    )


def sub_city_keyboard() -> InlineKeyboardMarkup:
    return optional_keyboard(CB_MANUAL_SKIP_SUB, skip_label="⏭ Not known")


def landmark_keyboard() -> InlineKeyboardMarkup:
    return optional_keyboard(CB_MANUAL_SKIP_LANDMARK, skip_label="⏭ Skip landmark")


def confirm_manual_keyboard() -> InlineKeyboardMarkup:
    """The last manual screen: save, go back, cancel."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Save this location", callback_data=CB_MANUAL_SAVE),
            ],
            [
                InlineKeyboardButton("⬅️ Back", callback_data=CB_BACK),
                InlineKeyboardButton("❌ Cancel", callback_data=CB_CANCEL),
            ],
        ]
    )


def duplicate_keyboard() -> InlineKeyboardMarkup:
    """A near match already exists: use it, or keep what was typed."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Use it", callback_data=CB_KEEP),
                InlineKeyboardButton("✏️ No, keep mine", callback_data=CB_RESTART),
            ],
            [InlineKeyboardButton("❌ Cancel", callback_data=CB_CANCEL)],
        ]
    )


__all__ = [
    "CB_BACK",
    "CB_CANCEL",
    "CB_CHOOSE",
    "CB_DONE",
    "CB_HOME_NO",
    "CB_HOME_YES",
    "CB_KEEP",
    "CB_MANUAL",
    "CB_MANUAL_CITY",
    "CB_MANUAL_CITY_OTHER",
    "CB_MANUAL_LANDMARK",
    "CB_MANUAL_NAME",
    "CB_MANUAL_SAVE",
    "CB_MANUAL_SKIP_LANDMARK",
    "CB_MANUAL_SKIP_SUB",
    "CB_MANUAL_SUB",
    "CB_MORE",
    "CB_REMOVE",
    "CB_RESTART",
    "CB_SEARCH",
    "chip_rows",
    "city_keyboard",
    "confirm_manual_keyboard",
    "duplicate_keyboard",
    "empty_keyboard",
    "error_keyboard",
    "home_area_keyboard",
    "landmark_keyboard",
    "manual_name_keyboard",
    "optional_keyboard",
    "results_keyboard",
    "search_keyboard",
    "sub_city_keyboard",
    "suggestion_rows",
]