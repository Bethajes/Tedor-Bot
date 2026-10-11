"""User-facing copy for the location selector.

Kept out of the handler so the wording can be reviewed without reading the
state machine, and so the three flows that share the selector show exactly the
same sentences. Numbers are in the text, never interpolated: there is nothing
variable to substitute and a stray ``{}`` in a prompt is a crash.
"""

from __future__ import annotations

from app.services.geocoding import attribution

# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

#: Shown when the selector opens. ``{mode}`` is an optional sentence explaining
#: why this step may be skipped.
PROMPT_SEARCH = (
    "📍 <b>Where can you tutor?</b>\n\n"
    "Type the area, neighbourhood or landmark — for example "
    "<code>Ayat</code>, <code>Bole</code> or <code>CMC</code>.\n\n"
    "{mode}\n\n"
    "Results come from " + attribution() + "."
)

#: Shown above a results list.
PROMPT_RESULTS = "Here is what I found — pick the right one, or add it manually:"

#: Shown when the provider answered and knew nothing. The fallback is offered
#: immediately rather than making the user try again.
PROMPT_EMPTY = (
    "I could not find that place.\n\n"
    "If you know it, add it manually below and an admin will review it."
)

#: Shown while a debounced search is in flight.
PROMPT_SEARCHING = "🔎 Searching…"

#: Shown once a keystroke is too short to be worth a request.
PROMPT_TOO_SHORT = "Keep typing — at least {length} characters."

#: Shown after a provider error. The message never says which provider or why.
PROMPT_ERROR = (
    "⚠️ The location search is unavailable right now.\n\n"
    "You can still add your location manually, and nothing you have already "
    "picked is lost."
)

#: Shown when the rate limiter rejects a search.
PROMPT_RATE_LIMITED = (
    "You are searching a lot. Please wait a moment, then try again — "
    "or add your location manually."
)

#: Shown when a button refers to results that are no longer held.
PROMPT_STALE = "That result has expired. Please search again."

# ---------------------------------------------------------------------------
# Already chosen
# ---------------------------------------------------------------------------

PROMPT_CHOSEN = "✅ Added. Pick another area, or press Done when finished."

#: A location the user typed rather than picked. Shown wherever a hand-typed
#: entry appears, so the two are never mistaken for each other.
PROMPT_UNVERIFIED_NOTE = (
    "✍️ <i>You typed this one yourself, so it is unverified until an admin "
    "reviews it. Everything still works normally.</i>"
)

#: A location the user typed rather than picked. Shown on every screen where a
#: hand-typed entry appears.
PROMP_UNVERIFIED_NOTE = (
    "✍️ <i>You typed this one yourself, so it is unverified until an admin "
    "reviews it. Everything still works normally.</i>"
)

# ---------------------------------------------------------------------------
# The manual form
# ---------------------------------------------------------------------------

PROMPT_MANUAL_INTRO = (
    "✍️ <b>Add your location manually</b>\n\n"
    "Tell me the name people actually use. An admin will review it before it "
    "is treated as an official place."
)

#: Pre-filled with whatever the user typed on the search screen.
PROMPT_MANUAL_NAME = (
    "📍 <b>Location name</b>\n\n"
    "I have kept what you typed — change it if it is wrong, or send it as it is."
)

PROMPT_MANUAL_CITY = (
    "🏙 <b>City</b>\n\nAddis Ababa is the usual choice. Pick another if your "
    "area is in a different city."
)

PROMPT_MANUAL_CITY_OTHER = (
    "🏙 <b>City</b>\n\nType the city, for example <code>Hawassa</code>."
)

PROMPT_MANUAL_SUB = (
    "🗺 <b>Sub-city or district</b> <i>(optional)</i>\n\n"
    "For example <code>Woreda 12</code> or <code>Addis Ketema</code>."
)

PROMPT_MANUAL_LANDMARK = (
    "📌 <b>Nearby landmark or description</b> <i>(optional)</i>\n\n"
    "Anything that helps someone find it, for example "
    "<code>near the local market</code>."
)

PROMPT_MANUAL_UNSUPPORTED_CITY = (
    "Sorry, we only operate in Ethiopia. Please choose one of the cities on "
    "the buttons, or add this location later when your city is supported."
)

# ---------------------------------------------------------------------------
# Confirmation and duplicates
# ---------------------------------------------------------------------------

PROMPT_MANUAL_CONFIRM = (
    "Does this look right?\n\n{details}\n\nSave it and it goes to an admin for "
    "review."
)

#: A near match already exists. Naming the existing one is the point: the user
#: should use it rather than adding a second row for the same place.
PROMPT_DUPLICATE = (
    "We may already have this place:\n\n{details}\n\nUse it instead, or keep "
    "what you typed and an admin will compare them."
)

PROMPT_DUPLICATE_ACCEPTED = "✅ Using the place we already have."

# ---------------------------------------------------------------------------
# The home-area question
# ---------------------------------------------------------------------------

PROMPT_HOME_AREA = (
    "You told me your home area is <b>{area}</b>.\n\n"
    "Would you also be willing to tutor there? I will not add it unless you "
    "say yes."
)

PROMPT_HOME_AREA_ACCEPTED = (
    "✅ Added your home area as a place you can tutor."
)
PROMPT_HOME_AREA_DECLINED = (
    "No problem. I will not use your address as a tutoring location."
)

# ---------------------------------------------------------------------------
# Finishing
# ---------------------------------------------------------------------------

PROMPT_DONE = "✅ Location saved."

#: An in-person flow will not accept an empty answer; this says so without
#: blocking progress, because Done simply stays unavailable.
PROMPT_NEED_ONE = "Please pick at least one area, or press Done if you are online only."


__all__ = [
    "PROMPT_EMPTY",
    "PROMPT_DUPLICATE",
    "PROMPT_DUPLICATE_ACCEPTED",
    "PROMPT_DONE",
    "PROMPT_ERROR",
    "PROMPT_HOME_AREA",
    "PROMPT_HOME_AREA_ACCEPTED",
    "PROMPT_HOME_AREA_DECLINED",
    "PROMPT_NEED_ONE",
    "PROMPT_MANUAL_CITY",
    "PROMPT_MANUAL_CITY_OTHER",
    "PROMPT_MANUAL_CONFIRM",
    "PROMPT_MANUAL_INTRO",
    "PROMPT_MANUAL_LANDMARK",
    "PROMPT_MANUAL_NAME",
    "PROMPT_MANUAL_SUB",
    "PROMPT_MANUAL_UNSUPPORTED_CITY",
    "PROMPT_RATE_LIMITED",
    "PROMPT_RESULTS",
    "PROMPT_SEARCH",
    "PROMPT_SEARCHING",
    "PROMPT_STALE",
    "PROMPT_TOO_SHORT",
    "PROMPT_UNVERIFIED_NOTE",
    "PROMPT_CHOSEN",
]
