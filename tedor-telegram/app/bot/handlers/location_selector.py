"""The reusable location selector: state, copy dispatch and provider search.

This module owns three things and nothing else:

* the session model the selector keeps in ``context.user_data``;
* the text and button handlers that move it between stages;
* the call to the geocoding provider, with the debounce in front of it.

The *wording* lives in :mod:`app.bot.handlers.location_prompts`, the *buttons*
live in :mod:`app.bot.keyboards.location`, and the *decisions about which rows
to write* belong to the flow that called in. Keeping them apart is what lets the
same selector serve tutor onboarding, the parent request flow and the admin job
generator without any of them having to know how the others work.

Two rules the stages exist to enforce:

* **Progress is never lost.** Switching from search to manual pre-fills the
  manual name with whatever was typed, and cancelling out of the manual form
  returns to the results rather than to the start.
* **Completion is explicit.** ``Done`` is a button, not a side effect of typing.

The debounce
------------

Typing "ayat tsebel" is ten keystrokes. A request per keystroke would spend ten
times the provider quota and answer only the last one, so each keystroke
reschedules one pending task and nothing is sent until the user pauses. The
task is stored on the session so a superseded keystroke cancels it, and it is
awaited or cancelled on exit so a search never outlives the screen.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from telegram import InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.bot.handlers import location_prompts
from app.bot.handlers.common import answer, safe_edit, safe_reply
from app.bot.handlers.location_prompts import (
    PROMPT_CHOSEN,
    PROMPT_MANUAL_NAME,
    PROMPT_UNVERIFIED_NOTE,
    PROMPT_DONE,
    PROMPT_DUPLICATE,
    PROMPT_DUPLICATE_ACCEPTED,
    PROMPT_EMPTY,
    PROMPT_ERROR,
    PROMPT_NEED_ONE,
    PROMPT_RATE_LIMITED,
    PROMPT_RESULTS,
    PROMPT_SEARCH,
    PROMPT_SEARCHING,
    PROMPT_STALE,
    PROMPT_TOO_SHORT,
)
from app.bot.keyboards import location as kb
from app.bot.keyboards.location import suggestion_rows
from app.config import settings
from app.database import session_scope
from app.logging_config import get_logger
from app.services import geocoding, location_service
from app.services import location_registry_service
from app.services.geocoding.base import PlaceSuggestion, clean_query

logger = get_logger(__name__)

__all__ = [
    "DebounceConfig",
    "LocationSaver",
    "LocationSession",
    "Selection",
    "Stage",
    "handle_callback",
    "handle_text",
    "on_enter",
]

#: Pause length before a keystroke becomes a search. Long enough that a fast
#: typist sends one request, short enough that it does not feel like lag.
DEBOUNCE_SECONDS: float = 0.45

#: Where the selector's state lives inside ``context.user_data``.
SESSION_KEY = "location_selector"

#: How long a stashed suggestion list stays usable. A button pressed on a
#: screen the user left ten minutes ago should fail cleanly rather than pick a
#: result nobody asked for.
SUGGESTION_TTL = 300


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


class Stage:
    """The stages of the selector.

    Plain string constants rather than an ``Enum``: they are compared against
    values that also live in session data, and a bad stage reads as a normal
    "start over" rather than as a crash.
    """

    SEARCH = "SEARCH"
    RESULTS = "RESULTS"
    MANUAL_NAME = "MANUAL_NAME"
    MANUAL_CITY = "MANUAL_CITY"
    MANUAL_CITY_OTHER = "MANUAL_CITY_OTHER"
    MANUAL_SUB = "MANUAL_SUB"
    MANUAL_LANDMARK = "MANUAL_LANDMARK"
    CONFIRM = "CONFIRM"
    DUPLICATE = "DUPLICATE"


@dataclass
class Selection:
    """One location the user has picked.

    Two provenances are possible and are never mixed up: a searched result came
    from the provider and carries an id and coordinates; a manual entry was
    typed, carries its raw text, and has neither.
    """

    name: str
    location_id: int | None = None
    source_text: str = ""
    provider_place_id: str = ""
    latitude: float | None = None
    longitude: float | None = None
    city: str = ""
    sub_city: str = ""
    normalized_name: str = ""

    @property
    def is_manual(self) -> bool:
        return bool(self.source_text) and not self.provider_place_id

    @property
    def label(self) -> str:
        return self.name

    @classmethod
    def from_stored(cls, item: Any) -> "Selection":
        """Rebuild a selection from a flow's already-saved value.

        Used when resuming an incomplete application or editing a profile, so
        the chips come back without a provider round-trip.
        """
        if isinstance(item, Selection):
            return cls(
                name=item.name,
                location_id=item.location_id,
                source_text=item.source_text,
                provider_place_id=item.provider_place_id,
                latitude=item.latitude,
                longitude=item.longitude,
                city=item.city,
                sub_city=item.sub_city,
                normalized_name=item.normalized_name,
            )
        return cls(name=" ".join(str(item or "").split()))


@dataclass
class LocationSession:
    """Everything the selector needs to resume exactly where it left off."""

    stage: str = Stage.SEARCH
    #: The user's typed query, preserved across a switch to manual entry.
    query: str = ""
    #: Stashed suggestions, with the monotonic time they were fetched at.
    suggestions: list[PlaceSuggestion] = field(default_factory=list)
    suggestions_at: float = 0.0
    #: What has already been chosen, in the order it was picked.
    selected: list[Selection] = field(default_factory=list)
    #: The manual form's fields, accumulated as each is answered.
    manual_name: str = ""
    manual_city: str = ""
    manual_sub_city: str = ""
    manual_landmark: str = ""
    #: A near-duplicate already in the database, offered as "use it instead".
    duplicate: dict[str, Any] | None = None
    #: ``ONLINE`` when the location is optional, so Done is always offered.
    teaching_mode: str = "IN_PERSON"
    #: How the flow names this field, so its payload key is unchanged.
    field: str = "locations"
    #: The in-flight debounce task, if any.
    search_task: Any = None

    # -- construction ---------------------------------------------------

    @classmethod
    def get(cls, context: Any) -> "LocationSession":
        """The session, created on first use."""
        session = context.user_data.get(SESSION_KEY)
        if not isinstance(session, LocationSession):
            session = LocationSession()
            context.user_data[SESSION_KEY] = session
        return session

    @classmethod
    def start(
        cls,
        context: Any,
        *,
        field: str = "locations",
        teaching_mode: str = "IN_PERSON",
    ) -> "LocationSession":
        """A fresh session, discarding any half-finished one."""
        session = cls(
            field=field, teaching_mode=str(teaching_mode or "IN_PERSON").upper()
        )
        context.user_data[SESSION_KEY] = session
        return session

    @classmethod
    def clear(cls, context: Any) -> None:
        session = context.user_data.get(SESSION_KEY)
        if isinstance(session, LocationSession) and session.search_task is not None:
            # The screen is going away; a search that lands afterwards has
            # nothing to attach to.
            task = session.search_task
            session.search_task = None
            if task is not None and not task.done():
                task.cancel()
        context.user_data.pop(SESSION_KEY, None)

    # -- suggestions ----------------------------------------------------

    def set_suggestions(self, suggestions: list[PlaceSuggestion], *, now: float) -> None:
        self.suggestions = list(suggestions)
        self.suggestions_at = now

    def suggestions_are_fresh(self, *, now: float) -> bool:
        return bool(self.suggestions) and (now - self.suggestions_at) < SUGGESTION_TTL

    def suggestion_at(self, index: int) -> PlaceSuggestion | None:
        if 0 <= index < len(self.suggestions):
            return self.suggestions[index]
        return None

    # -- selection ------------------------------------------------------

    def pick(self, selection: Selection) -> bool:
        """Add a location unless the same one is already chosen.

        Identity is the canonical id where there is one, and the name plus city
        otherwise, so a manual entry made twice under two spellings still shows
        up as two chips — the review queue, not the chip list, deduplicates it.
        """
        for existing in self.selected:
            if existing.location_id and existing.location_id == selection.location_id:
                return False
            if (
                not selection.location_id
                and existing.name.casefold() == selection.name.casefold()
                and existing.city.casefold() == selection.city.casefold()
            ):
                return False
        self.selected.append(selection)
        return True

    def remove(self, index: int) -> bool:
        if 0 <= index < len(self.selected):
            del self.selected[index]
            return True
        return False

    # -- manual form ----------------------------------------------------

    def reset_manual(self) -> None:
        """Empty the manual form without touching what has already been picked."""
        self.manual_name = ""
        self.manual_city = ""
        self.manual_sub_city = ""
        self.manual_landmark = ""
        self.duplicate = None

    @property
    def is_online(self) -> bool:
        return self.teaching_mode == "ONLINE"

    @property
    def can_finish(self) -> bool:
        """Whether Done is meaningful now.

        An in-person flow needs at least one location; an online-only tutor may
        finish with none at all, because location is not their constraint.
        """
        return bool(self.selected) if not self.is_online else True

    def payload(self) -> dict[str, Any]:
        """The values to merge into the owning flow's payload.

        Two shapes come out, because the callers want different things: the
        onboarding and parent flows read ``name``/``source_text``, while the
        database and the matching engine read the canonical ids. Returning both
        means neither has to re-derive the other.
        """
        return {
            "locations": [item.label for item in self.selected],
            "location_selections": list(self.selected),
            "location_ids": [
                item.location_id for item in self.selected if item.location_id
            ],
            "location_source_texts": [
                item.source_text for item in self.selected if item.source_text
            ],
        }


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DebounceConfig:
    """The timings the selector uses.

    A dataclass rather than bare constants so the tests can shrink them: a
    450 ms debounce is awkward to test and irrelevant to what is being checked.
    """

    debounce: float = DEBOUNCE_SECONDS
    min_length: int = geocoding.MIN_QUERY_LENGTH

    @classmethod
    def from_settings(cls) -> "DebounceConfig":
        return cls()


class LocationSaver(Protocol):
    """What the owning flow supplies to write the user's picks to the database.

    A callable rather than an import so each flow controls its own transaction:
    the selector never opens a session it cannot close.
    """

    async def __call__(
        self, name: str, city: str, sub_city: str, landmark: str
    ) -> Any: ...


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _now() -> float:
    return time.monotonic()


async def on_enter(
    update: Any,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    state: int,
    field: str = "locations",
    teaching_mode: str = "IN_PERSON",
    preselect: Any = None,
    config: DebounceConfig | None = None,
) -> int:
    """Open the selector. Returns the same state while the user works.

    ``preselect`` is a sequence of already-chosen locations — for resuming an
    incomplete application, or editing a saved profile — so previous choices
    come back as chips instead of being quietly forgotten.
    """
    session = LocationSession.start(
        context, field=field, teaching_mode=teaching_mode
    )
    if preselect:
        for item in preselect:
            session.pick(Selection.from_stored(item))

    mode = (
        "Since you are teaching online this is optional — you can skip it."
        if session.is_online
        else ""
    )
    await safe_reply(
        update,
        PROMPT_SEARCH.format(mode=mode),
        kb.search_keyboard(),
        parse_mode=ParseMode.HTML,
    )
    return state


async def handle_text(
    update: Any,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    state: int,
    save: LocationSaver | None = None,
    config: DebounceConfig | None = None,
) -> int | None:
    """A typed answer at the location step.

    What it means depends on the stage: at SEARCH it starts a query, inside the
    manual form it is that form's answer.
    """
    timings = config or DebounceConfig()
    session = LocationSession.get(context)
    text = (update.message.text or "").strip()

    if session.stage == Stage.MANUAL_NAME:
        session.manual_name = text
        return await _enter_manual_city(update, context, session, state)

    if session.stage == Stage.MANUAL_CITY_OTHER:
        session.manual_city = text
        session.stage = Stage.MANUAL_SUB
        return await _show_manual_sub(update, context, session, state)

    if session.stage == Stage.MANUAL_SUB:
        session.manual_sub_city = text
        return await _enter_manual_landmark(update, context, session, state)

    if session.stage == Stage.MANUAL_LANDMARK:
        session.manual_landmark = text
        return await _confirm_manual(update, context, session, state, save)

    entries = _split_entries(text)
    if len(entries) > 1:
        # More than one place at once. This is the shape the applications have
        # always accepted, so it stays accepted: each entry is resolved against
        # the registry, and only the ones that match become chips. Continuing to
        # honour it is what keeps a tutor who types "Ayat, Bole" from being told
        # the interface changed under them.
        return await _take_many(update, context, session, state, entries)

    if _is_known_area(entries):
        # One place the platform already knows. A tutor or admin who types
        # "Ayat" and presses send has finished answering, so waiting for them
        # to also press Done would be a step backwards.
        return await _take_many(update, context, session, state, entries)

    return await _search(update, context, session, state, text, timings)


def _is_known_area(entries: list[str]) -> bool:
    """Whether every typed area is already a location we hold.

    A name we have never seen is a search, not an answer: sending it to the
    provider is what finds the place, and treating it as a finished answer
    would just be an empty form field.
    """
    if not entries:
        return False
    with session_scope() as db_session:
        for entry in entries:
            if location_registry_service.resolve_by_text(db_session, entry) is None:
                spec = location_service.LOCATIONS.get(
                    location_service.normalize_location(entry) or ""
                )
                if spec is None:
                    return False
    return True


def _split_entries(text: str) -> list[str]:
    """Comma- or semicolon-separated areas, trimmed and de-duplicated."""
    parts = [part.strip() for part in re.split(r"[,;/]", text)]
    seen: list[str] = []
    for part in parts:
        if part and part.casefold() not in {item.casefold() for item in seen}:
            seen.append(part)
    return seen


async def _take_many(
    update: Any,
    context: ContextTypes.DEFAULT_TYPE,
    session: LocationSession,
    state: int,
    entries: list[str],
) -> int:
    """Accept several typed areas at once, resolving each against our records.

    Only entries that match a known location are added. A name we have never
    seen is not silently dropped and not silently created: the user is told
    which one needs the manual form, and everything else keeps working.

    Resolution tries the database first and then the curated registry that
    ships in source. The second lookup is what keeps this working on a fresh
    install that has not been seeded yet, and it never invents a row: a name
    recognised only in code is stored exactly as the user typed it, with the
    canonical key attached so the matcher compares it correctly.
    """
    resolved: list[Selection] = []
    unmatched: list[str] = []

    with session_scope() as db_session:
        for entry in entries:
            location = location_registry_service.resolve_by_text(db_session, entry)
            if location is not None:
                resolved.append(
                    Selection(
                        name=location.name,
                        location_id=location.id,
                        city=location.city,
                        normalized_name=location.normalized_name,
                    )
                )
                continue
            # Not in the database. The in-code registry is the same curated
            # set the matching engine reads, so a hit here is a real place.
            key = location_service.normalize_location(entry)
            spec = location_service.LOCATIONS.get(key) if key else None
            if spec is None:
                unmatched.append(entry)
                continue
            resolved.append(
                Selection(
                    name=spec.name,
                    source_text=entry,
                    city=_city_of_spec(spec),
                    normalized_name=spec.key,
                )
            )

    for selection in resolved:
        session.pick(selection)

    if unmatched and not resolved:
        # Nothing matched at all, so this was probably a search for a single
        # place we do not know. Hand it to the search path rather than
        # reporting failure on something the user typed as a list.
        return await _search(
            update, context, session, state, entries[0], DebounceConfig()
        )

    await _show_results(update, context, session, state)
    if unmatched:
        await safe_reply(
            update,
            "I could not match "
            + ", ".join(f"<code>{name}</code>" for name in unmatched)
            + ". Use <b>Add it manually</b> below for those.",
            kb.empty_keyboard(show_done=session.can_finish),
            parse_mode=ParseMode.HTML,
        )
    # A list of areas typed in one go is a complete answer: the tutor said
    # everything they wanted to say, so the flow commits and moves on rather
    # than making them press Done as well.
    return done


def _city_of_spec(spec: Any) -> str:
    """The city a curated registry entry belongs to."""
    if str(spec.zone) == str(location_service.LocationZone.OUTSIDE):
        return spec.name
    return location_registry_service.COUNTRY_ETHIOPIA_DEFAULT_CITY


async def handle_callback(
    update: Any,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    state: int,
    save: LocationSaver | None = None,
    config: DebounceConfig | None = None,
) -> int | None:
    """A button on the selector.

    Returns the ``state`` recorded by the caller while the selector keeps
    working, and the "done" state once the user presses Done — or ``None`` when
    nothing this module handles happened and the conversation's own dispatch
    should carry on.
    """
    timings = config or DebounceConfig()
    query = update.callback_query
    if query is None:
        return None
    data = query.data or ""
    session = LocationSession.get(context)

    if data == kb.CB_SEARCH:
        return await _prompt_search(update, context, session, state)

    if data.startswith(kb.CB_CHOOSE):
        return await _choose(update, context, session, state, data)

    if data.startswith(kb.CB_MORE):
        return await _page(update, context, session, state, data)

    if data.startswith(kb.CB_REMOVE):
        return await _remove_chip(update, context, session, state, data)

    if data == kb.CB_MANUAL:
        session.query = (session.query or "").strip()
        session.reset_manual()
        session.manual_name = session.query
        session.stage = Stage.MANUAL_NAME
        return await _show_manual_name(update, context, session, state)

    if data == kb.CB_MANUAL_CITY_OTHER:
        session.stage = Stage.MANUAL_CITY_OTHER
        await safe_edit(
            update,
            location_prompts.PROMPT_MANUAL_CITY_OTHER,
            kb.optional_keyboard(kb.CB_MANUAL_SAVE, skip_label="✏️ Use default"),
            parse_mode=ParseMode.HTML,
        )
        return state

    if data.startswith("loc:city:"):
        # A city button: index-based like every other picker, so no city name
        # ever has to fit inside the 64-byte callback budget.
        index_text = data.rsplit(":", 1)[-1]
        session.manual_city = _city_chosen(update, context, index_text)
        session.stage = Stage.MANUAL_SUB
        return await _show_manual_sub(update, context, session, state)

    if data == kb.CB_MANUAL_SKIP_SUB:
        session.manual_sub_city = ""
        return await _enter_manual_landmark(update, context, session, state)

    if data == kb.CB_MANUAL_SKIP_LANDMARK:
        session.manual_landmark = ""
        return await _confirm_manual(update, context, session, state, save)

    if data == kb.CB_MANUAL_SAVE:
        return await _confirm_manual(update, context, session, state, save)

    if data == kb.CB_KEEP:
        return await _accept_duplicate(update, context, session, state, save)

    if data == kb.CB_RESTART:
        session.reset_manual()
        session.manual_name = session.query
        session.stage = Stage.MANUAL_NAME
        return await _show_manual_name(update, context, session, state)

    if data == kb.CB_BACK:
        return await _back(update, context, session, state)

    if data == kb.CB_DONE:
        if not session.can_finish:
            # Online-only tutors may finish empty; everyone else must pick
            # something, so Done is the button that says "enough".
            await answer(update, PROMPT_NEED_ONE, show_alert=True)
            return state
        return done

    return None


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


class _Done(int):
    """Marks the callback return as "the selector is finished".

    A bare ``int`` subclass because the owning conversations compare their
    returned state with ``==`` against their own state ids: a ``str`` would
    never match and would leave the user stuck on the location screen forever.
    """

    __slots__ = ()


#: The value ``handle_callback`` returns once the user presses Done. Callers
#: treat any non-``None`` return as "finished, advance the conversation".
done = _Done(-1)


async def _search(
    update: Any,
    context: ContextTypes.DEFAULT_TYPE,
    session: LocationSession,
    state: int,
    text: str,
    timings: DebounceConfig,
) -> int:
    """Schedule (or reschedule) the debounced search for ``text``.

    Nothing is sent here. The keystroke is remembered and one task is queued;
    a later keystroke cancels it, which is what makes the debounce work.
    """
    query = clean_query(text)
    if len(query) < timings.min_length:
        await safe_reply(
            update,
            PROMPT_TOO_SHORT.format(length=timings.min_length),
            kb.empty_keyboard(show_done=session.can_finish),
            parse_mode=ParseMode.HTML,
        )
        return state

    session.query = query
    session.stage = Stage.SEARCH

    if session.search_task is not None and not session.search_task.done():
        session.search_task.cancel()

    await safe_reply(
        update,
        PROMPT_SEARCHING,
        kb.empty_keyboard(show_done=session.can_finish),
        parse_mode=ParseMode.HTML,
    )

    session.search_task = asyncio.get_running_loop().create_task(
        _run_search(
            update.message.chat_id if update.message is not None else None,
            context,
            query,
            timings,
        )
    )
    return state


async def _run_search(
    chat_id: int | None,
    context: ContextTypes.DEFAULT_TYPE,
    query: str,
    timings: DebounceConfig,
) -> None:
    """Perform the debounced provider call and render whatever came back.

    Runs detached from the triggering update, which is the only way a pause can
    outlive a keystroke. Every failure path is a message, never a raised task:
    an unhandled task exception in PTB is silent from the user's point of view.
    """
    session = LocationSession.get(context)
    try:
        if timings.debounce:
            await asyncio.sleep(timings.debounce)
        now = _now()
        try:
            suggestions = await geocoding.search_places(
                query, rate_key=str(_rate_key(context))
            )
        except geocoding.GeocodingNotConfigured:
            await _send(context, chat_id, PROMPT_ERROR, kb.error_keyboard())
            return
        except geocoding.GeocodingRateLimited:
            await _send(context, chat_id, PROMPT_RATE_LIMITED, kb.error_keyboard())
            return
        except geocoding.GeocodingError:
            # The exception's own message is never shown to the user: it can
            # hold provider detail, and the fallback is the useful part.
            await _send(context, chat_id, PROMPT_ERROR, kb.error_keyboard())
            return

        session.set_suggestions(suggestions, now=now)
        session.stage = Stage.RESULTS

        if not suggestions:
            await _send(
                context,
                chat_id,
                PROMPT_EMPTY,
                kb.empty_keyboard(show_done=session.can_finish),
            )
            return

        await _send(
            context,
            chat_id,
            PROMPT_RESULTS,
            kb.results_keyboard(suggestions, show_done=session.can_finish),
        )
    except asyncio.CancelledError:
        # A newer keystroke replaced this search.
        return
    finally:
        if session.search_task is asyncio.current_task():
            session.search_task = None


def _rate_key(context: ContextTypes.DEFAULT_TYPE) -> object:
    """The caller identity the rate limiter keys on.

    The chat id, never the user id: the limit is about the conversation's
    search volume, and the chat id is already what every other per-user guard
    in this codebase uses.
    """
    effective = getattr(context, "_effective_chat", None) or {}
    return effective.get("id", 0)


async def _send(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int | None,
    text: str,
    keyboard: Any,
) -> None:
    """Send a message from a detached task."""
    if chat_id is None:
        return
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )
    except Exception:  # pragma: no cover - network
        logger.warning("Could not deliver a location selector message")


# ---------------------------------------------------------------------------
# Results, chips and paging
# ---------------------------------------------------------------------------


async def _choose(
    update: Any,
    context: ContextTypes.DEFAULT_TYPE,
    session: LocationSession,
    state: int,
    data: str,
) -> int:
    """Add a suggestion to the selection and re-render the chips."""
    await answer(update)
    index_text = data.rsplit(":", 1)[-1]
    if not index_text.isdigit():
        return state
    if not session.suggestions_are_fresh(now=_now()):
        await safe_edit(
            update,
            PROMPT_STALE,
            kb.empty_keyboard(show_done=session.can_finish),
            parse_mode=ParseMode.HTML,
        )
        return state

    suggestion = session.suggestion_at(int(index_text))
    if suggestion is None:
        return state

    session.pick(_selection_from_suggestion(suggestion))
    await _show_results(update, context, session, state)
    return state


async def _page(
    update: Any,
    context: ContextTypes.DEFAULT_TYPE,
    session: LocationSession,
    state: int,
    data: str,
) -> int:
    """Show the next or previous page of suggestions.

    The index travels in the callback, so a stale page button can never ask for
    a start beyond the list.
    """
    await answer(update)
    if not session.suggestions_are_fresh(now=_now()):
        await safe_edit(
            update,
            PROMPT_STALE,
            kb.empty_keyboard(show_done=session.can_finish),
            parse_mode=ParseMode.HTML,
        )
        return state
    start_text = data.rsplit(":", 1)[-1]
    start = int(start_text) if start_text.isdigit() else 0
    await safe_edit(
        update,
        PROMPT_RESULTS,
        kb.results_keyboard(
            session.suggestions,
            page=start,
            show_done=session.can_finish,
        ),
        parse_mode=ParseMode.HTML,
    )
    return state


async def _remove_chip(
    update: Any,
    context: ContextTypes.DEFAULT_TYPE,
    session: LocationSession,
    state: int,
    data: str,
) -> int:
    await answer(update)
    index_text = data.rsplit(":", 1)[-1]
    if index_text.isdigit():
        session.remove(int(index_text))
    await _show_results(update, context, session, state)
    return state


def _selection_from_suggestion(suggestion: PlaceSuggestion) -> Selection:
    return Selection(
        name=suggestion.name.strip(),
        provider_place_id=suggestion.place_id,
        latitude=suggestion.latitude,
        longitude=suggestion.longitude,
        city=suggestion.city,
        sub_city=suggestion.sub_city,
        # The normalised name is how the database row will be found, and how a
        # re-created row is recognised as the same place.
        normalized_name=location_service.normalize_location(suggestion.name) or "",
    )


async def _show_results(
    update: Any,
    context: ContextTypes.DEFAULT_TYPE,
    session: LocationSession,
    state: int,
) -> int:
    """Render whichever screen fits the current state.

    Three cases: results to show, chips with nothing new to show, or the search
    prompt when there are neither. All three keep the manual fallback reachable.
    """
    chips = _chip_text(session)
    if session.suggestions and session.suggestions_are_fresh(now=_now()):
        await safe_edit(
            update,
            PROMPT_RESULTS + chips,
            _with_chip_rows(
                kb.results_keyboard(
                    session.suggestions, show_done=session.can_finish
                ),
                session,
            ),
            parse_mode=ParseMode.HTML,
        )
        return state
    if session.selected:
        await safe_edit(
            update,
            PROMPT_CHOSEN + chips,
            _with_chip_rows(
                kb.empty_keyboard(show_done=session.can_finish), session
            ),
            parse_mode=ParseMode.HTML,
        )
        return state
    await _prompt_search(update, context, session, state)
    return state


def _with_chip_rows(
    keyboard: InlineKeyboardMarkup, session: LocationSession
) -> InlineKeyboardMarkup:
    """Put a remove button under each already-selected area.

    Requirement: a tutor who selected an area by mistake has to be able to take
    it back. The list was previously rendered as text only, so the chips read as
    a confirmation rather than as something editable — picking an area felt
    permanent for the rest of the conversation.

    Only the remove button is interactive; the name beside it is inert, so a
    stray tap cannot re-add or re-order anything.
    """
    if not session.selected:
        return keyboard
    entries = [
        {"name": selection.name} for selection in session.selected
    ]
    return InlineKeyboardMarkup(
        list(kb.chip_rows(entries)) + list(keyboard.inline_keyboard)
    )


def _chip_text(session: LocationSession) -> str:
    """The chip list as plain text, including the unverified markers.

    Shown alongside the results rather than instead of them, so a tutor picking
    three areas can still see a suggestion.
    """
    if not session.selected:
        return ""
    lines = ["", "<b>Already selected</b> (tap ❌ to remove one):"]
    for selection in session.selected:
        marker = " ✍️ unverified" if selection.is_manual else ""
        detail = selection.sub_city or selection.city
        suffix = f" ({detail})" if detail else ""
        lines.append(f"• {selection.name}{suffix}{marker}")
    return "\n".join(lines)


async def _prompt_search(
    update: Any,
    context: ContextTypes.DEFAULT_TYPE,
    session: LocationSession,
    state: int,
) -> int:
    mode = (
        "Since you are teaching online this is optional — you can skip it."
        if session.is_online
        else ""
    )
    await safe_edit(
        update,
        PROMPT_SEARCH.format(mode=mode),
        kb.search_keyboard(),
        parse_mode=ParseMode.HTML,
    )
    return state


# ---------------------------------------------------------------------------
# Manual entry
# ---------------------------------------------------------------------------


async def _show_manual_name(
    update: Any,
    context: ContextTypes.DEFAULT_TYPE,
    session: LocationSession,
    state: int,
) -> int:
    """The first manual screen, pre-filled with whatever was typed on search.

    The pre-fill is the point: a user who searched for "Woreda 12" and got
    nothing should not have to type it again.
    """
    await safe_reply(
        update,
        PROMPT_MANUAL_NAME
        + (f"\n\nCurrently: <code>{session.manual_name}</code>" if session.manual_name else ""),
        kb.manual_name_keyboard(),
        parse_mode=ParseMode.HTML,
    )
    return state


async def _enter_manual_city(
    update: Any,
    context: ContextTypes.DEFAULT_TYPE,
    session: LocationSession,
    state: int,
) -> int:
    session.stage = Stage.MANUAL_CITY
    session.manual_city = ""
    await safe_reply(
        update,
        location_prompts.PROMPT_MANUAL_CITY,
        kb.city_keyboard(_cities()),
        parse_mode=ParseMode.HTML,
    )
    return state


async def _show_manual_sub(
    update: Any,
    context: ContextTypes.DEFAULT_TYPE,
    session: LocationSession,
    state: int,
) -> int:
    session.stage = Stage.MANUAL_SUB
    await safe_reply(
        update,
        location_prompts.PROMPT_MANUAL_SUB,
        kb.sub_city_keyboard(),
        parse_mode=ParseMode.HTML,
    )
    return state


async def _enter_manual_landmark(
    update: Any,
    context: ContextTypes.DEFAULT_TYPE,
    session: LocationSession,
    state: int,
) -> int:
    session.stage = Stage.MANUAL_LANDMARK
    await safe_reply(
        update,
        location_prompts.PROMPT_MANUAL_LANDMARK,
        kb.landmark_keyboard(),
        parse_mode=ParseMode.HTML,
    )
    return state


def _cities() -> tuple[str, ...]:
    """Cities offered as buttons, Addis Ababa first."""
    return tuple(
        sorted(location_registry_service.ETHIOPIAN_CITIES, key=_addis_first)
    )


def _addis_first(city: str) -> tuple[int, str]:
    return (0 if city.casefold() == "addis ababa" else 1, city.casefold())


def _city_chosen(update: Any, context: ContextTypes.DEFAULT_TYPE, index_text: str) -> str:
    cities = _cities()
    if index_text.isdigit() and 0 <= int(index_text) < len(cities):
        return cities[int(index_text)]
    return location_registry_service.COUNTRY_ETHIOPIA_DEFAULT_CITY


# ---------------------------------------------------------------------------
# Saving a manual location
# ---------------------------------------------------------------------------


async def _confirm_manual(
    update: Any,
    context: ContextTypes.DEFAULT_TYPE,
    session: LocationSession,
    state: int,
    save: LocationSaver | None,
) -> int:
    """Write the manual location, or offer the near-duplicate we already have.

    The duplicate check is the whole reason this is a separate step rather than
    a straight insert: two users naming "Goro Sefer" should produce one row and
    a suggestion, not two rows an admin has to merge by hand.
    """
    name = session.manual_name.strip()
    if not name:
        await answer(update, "Please type the location name.", show_alert=True)
        session.stage = Stage.MANUAL_NAME
        return state

    if save is None:
        # No saver configured: the flow owns persistence and has not wired it up
        # yet, which is a deployment bug rather than a user error.
        logger.error("No location saver configured for the manual form")
        await safe_reply(
            update,
            PROMPT_ERROR,
            kb.error_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return state

    try:
        resolved = await save(
            name, session.manual_city, session.manual_sub_city, session.manual_landmark
        )
    except location_registry_service.LocationError as exc:
        # The service's own message is user-facing copy and safe to show.
        await answer(update, exc.user_message, show_alert=True)
        session.stage = Stage.MANUAL_NAME
        return state
    except Exception:
        await safe_reply(
            update,
            PROMPT_ERROR,
            kb.error_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return state

    if resolved is not None and resolved.get("is_suggestion"):
        # Already have it. Hand the user a confirm step so they choose, rather
        # than silently attaching a row they did not pick.
        session.duplicate = resolved
        session.stage = Stage.DUPLICATE
        await safe_reply(
            update,
            PROMPT_DUPLICATE.format(details=_describe(resolved)),
            kb.duplicate_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return state

    session.pick(_selection_from_result(resolved))
    session.reset_manual()
    session.stage = Stage.RESULTS
    await safe_reply(
        update,
        PROMPT_UNVERIFIED_NOTE,
        kb.empty_keyboard(show_done=session.can_finish),
        parse_mode=ParseMode.HTML,
    )
    await _show_results(update, context, session, state)
    return state


async def _accept_duplicate(
    update: Any,
    context: ContextTypes.DEFAULT_TYPE,
    session: LocationSession,
    state: int,
    save: LocationSaver | None,
) -> int:
    """Attach the location we already have, in place of the typed one."""
    await answer(update)
    if session.duplicate is None:
        return state
    session.pick(_selection_from_result(session.duplicate))
    session.reset_manual()
    session.stage = Stage.RESULTS
    await safe_reply(update, PROMPT_DUPLICATE_ACCEPTED)
    await _show_results(update, context, session, state)
    return state


def _selection_from_result(result: dict[str, Any] | None) -> Selection:
    """A chip from what the owning flow's saver reported.

    The saver's return shape is part of :class:`LocationSaver`'s contract: the
    ``Location`` row it wrote, as a plain dict, plus provenance. Converting it
    here means no ORM object escapes the saver's session.
    """
    if not result:
        return Selection(name="")
    return Selection(
        name=result.get("name") or "",
        location_id=result.get("location_id"),
        source_text=result.get("source_text") or "",
        provider_place_id=result.get("provider_place_id") or "",
        latitude=result.get("latitude"),
        longitude=result.get("longitude"),
        city=result.get("city") or "",
        sub_city=result.get("sub_city") or "",
        normalized_name=result.get("normalized_name") or "",
    )


def _describe(result: dict[str, Any] | None) -> str:
    """One line describing an existing location, for the duplicate prompt."""
    if not result:
        return ""
    name = result.get("name") or ""
    city = result.get("city") or ""
    sub_city = result.get("sub_city") or ""
    landmark = result.get("landmark") or ""
    parts = [f"📍 <b>{name}</b>"]
    if sub_city:
        parts.append(sub_city)
    if city:
        parts.append(city)
    if landmark:
        parts.append(f"near {landmark}")
    return " — ".join(parts)


async def _back(
    update: Any,
    context: ContextTypes.DEFAULT_TYPE,
    session: LocationSession,
    state: int,
) -> int:
    """Move one stage back inside the manual form, or out of it entirely.

    Leaving the manual form returns to the results rather than to the search
    prompt, so pressing Back out of a dead end does not cost the results.
    """
    if session.stage == Stage.MANUAL_NAME:
        session.stage = Stage.RESULTS
        await _show_results(update, context, session, state)
        return state
    if session.stage == Stage.MANUAL_CITY:
        session.stage = Stage.MANUAL_NAME
        return await _show_manual_name(update, context, session, state)
    if session.stage == Stage.MANUAL_SUB:
        session.stage = Stage.MANUAL_CITY
        return await _enter_manual_city(update, context, session, state)
    if session.stage == Stage.MANUAL_LANDMARK:
        session.stage = Stage.MANUAL_SUB
        return await _show_manual_sub(update, context, session, state)
    if session.stage == Stage.DUPLICATE:
        session.stage = Stage.MANUAL_NAME
        return await _show_manual_name(update, context, session, state)
    return await _prompt_search(update, context, session, state)


# ---------------------------------------------------------------------------
# The home-area question
# ---------------------------------------------------------------------------


async def ask_about_home_area(
    update: Any,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    state: int,
    home_area: str,
) -> int | None:
    """Ask whether a tutor will also teach at the address they gave us.

    This is the one case where a location is *offered* rather than asked for,
    and it is deliberately not a shortcut: a home address is private, is used
    for archival only, and must never be turned into a tutoring location by a
    default. The area becomes one only when the tutor presses Yes here.

    Returns ``None`` when there is nothing worth asking — no address on file, or
    addresses that are already among their tutoring locations.
    """
    area = " ".join(str(home_area or "").split())
    if not area:
        return None

    session = LocationSession.get(context)
    for selection in session.selected:
        if _same_area(selection.name, area):
            return None

    pending[PROMPT_KEY] = area
    await safe_reply(
        update,
        location_prompts.PROMPT_HOME_AREA.format(area=_escape(area)),
        kb.home_area_keyboard(),
        parse_mode=ParseMode.HTML,
    )
    return state


async def answer_about_home_area(
    update: Any,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    state: int,
    answer_text: str,
) -> int:
    """Handle the Yes/No answer, adding the area only on an explicit Yes."""
    session = LocationSession.get(context)
    area = pending.pop(PROMPT_KEY, "")
    accepted = answer_text.casefold() in ("yes", "y", "true")

    if not accepted:
        # The only correct response to a No: remember it, and do nothing else.
        await safe_reply(update, location_prompts.PROMPT_HOME_AREA_DECLINED)
        return state

    if not area:
        # The address was withdrawn before the answer arrived. Refusing quietly
        # is better than inventing one from a stale key.
        return state

    with session_scope() as db_session:
        location = location_registry_service.resolve_by_text(db_session, area)
        name = location.name if location is not None else area
        location_id = location.id if location is not None else None
        city = location.city if location is not None else ""
        key = (
            location.normalized_name
            if location is not None
            else location_service.normalize_location(area) or ""
        )

    session.pick(
        Selection(
            name=name,
            location_id=location_id,
            # The address is kept as the source text, so the row can always be
            # traced back to what the tutor actually told us.
            source_text=area,
            city=city,
            normalized_name=key,
        )
    )
    await safe_reply(update, location_prompts.PROMPT_HOME_AREA_ACCEPTED)
    return state


def home_area_pending(context: ContextTypes.DEFAULT_TYPE) -> str:
    """The address waiting for an answer, if any."""
    return pending.get(PROMPT_KEY, "")


def clear_home_area(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Forget a pending home-area question. Cancelling the flow clears it."""
    pending.pop(PROMPT_KEY, None)


#: Addresses awaiting a Yes/No, keyed by chat id. A module-level dict rather
#: than session data because the question can outlive the conversation that
#: asked it (the tutor may answer at the summary screen).
pending: dict[int, str] = {}
PROMPT_KEY = "home_area"


def _same_area(name: str, area: str) -> bool:
    """Whether a chip already covers this address, spelling aside."""
    left = location_service.normalize_location(name) or ""
    right = location_service.normalize_location(area) or ""
    return bool(left) and left == right


def _escape(value: str) -> str:
    """Escape HTML the user typed, so their address cannot break the markup."""
    return (
        value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
