"""Admin review of locations the platform has never seen before.

A tutor or parent who cannot find their area types it by hand, which creates a
``MANUAL`` location in ``PENDING``. This module is the queue that reviews those.

Five things an admin can do here, and the order they are offered in is the order
of risk:

* **Approve** — the place is real and worth keeping.
* **Reject** — it is invalid or misleading. It stays stored, because a job that
  already points at it must still resolve, but it is no longer offered.
* **Merge** — it duplicates an existing location. Tutor links, job links and
  aliases are moved across in one transaction, and the loser is kept as a
  ``REJECTED`` tombstone rather than deleted, so a report written before the
  merge still resolves.
* **Add a spelling** — an alias, so the next person searching a variant spelling
  finds the right row instead of typing it again.
* **Set coordinates** — only when the position is *established*. This is the
  only route to a manual location ever gaining coordinates, which is why it
  refuses anything outside Ethiopia.

Nothing here writes ``verification_status`` for a user-facing request, and the
whole module is guarded by :func:`require_admin`, so a tutor can never mark
their own location verified.
"""

from __future__ import annotations

from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from app.bot.handlers.common import answer, safe_reply
from app.bot.keyboards.admin import (
    CB_ADMIN_LOCATIONS,
    CB_LOC_LINK,
    CB_LOC_UNLINKED,
    CB_ADMIN_MENU,
    CB_LOC_ALIAS,
    CB_LOC_APPROVE,
    CB_LOC_COORDS,
    CB_LOC_DETAIL,
    CB_LOC_MERGE,
    CB_LOC_MERGE_DO,
    CB_LOC_PAGE,
    CB_LOC_REJECT,
    CB_LOC_REJECT_CONFIRM,
    LOCATIONS_PER_PAGE,
    location_detail_keyboard,
    location_list_keyboard,
    location_merge_keyboard,
    location_reject_reason_keyboard,
    location_unlinked_keyboard,
    locations_menu_keyboard,
)
from app.bot.states import LocationAdminStates
from app.config import settings
from app.database import session_scope
from app.logging_config import get_logger
from app.services import location_registry_service as locations
from app.services.location_registry_service import LocationError

logger = get_logger(__name__)

__all__ = [
    "LocationAdminStates",
    "build_location_review_conversation",
    "callback_handler",
    "location_review_command",
]

#: Transient review state, keyed by the pending action.
_KEY_LOCATION = "location_review"

#: Shown after a rejection reason is recorded.
PROMPT_REASON_KEEP = "Rejecting with no reason."


# ---------------------------------------------------------------------------
# entry points
# ---------------------------------------------------------------------------


async def location_review_command(
    update: Any, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """``/locations`` — the review queue. Admins only."""
    if not await require_admin(update):
        return ConversationHandler.END
    return await _show_menu(update, context)


def build_location_review_conversation() -> ConversationHandler:
    """The review conversation, registered before the admin console.

    It has to come first: the admin conversation's fallback callback handler is
    unfiltered and would swallow every ``admin:loc:*`` button before this module
    ever saw one. The same reasoning puts the ``match:`` and ``japply:``
    handlers there.
    """
    states: dict[int, list[Any]] = {
        int(LocationAdminStates.LIST): [CallbackQueryHandler(callback_handler)],
        int(LocationAdminStates.DETAIL): [CallbackQueryHandler(callback_handler)],
        int(LocationAdminStates.MERGE_PICK): [CallbackQueryHandler(callback_handler)],
        int(LocationAdminStates.REASON_INPUT): [
            CallbackQueryHandler(callback_handler),
            MessageHandler(filters.TEXT & ~filters.COMMAND, _reason_handler),
        ],
        int(LocationAdminStates.ALIAS_INPUT): [
            CallbackQueryHandler(callback_handler),
            MessageHandler(filters.TEXT & ~filters.COMMAND, _alias_handler),
        ],
        int(LocationAdminStates.COORDS_INPUT): [
            CallbackQueryHandler(callback_handler),
            MessageHandler(filters.TEXT & ~filters.COMMAND, _coords_handler),
        ],
    }
    return ConversationHandler(
        entry_points=[CommandHandler("locations", location_review_command)],
        states=states,
        fallbacks=[
            MessageHandler(filters.COMMAND, lambda *_: ConversationHandler.END),
        ],
        name="location_review",
        allow_reentry=True,
        per_message=False,
    )


async def require_admin(update: Any) -> bool:
    """Whether the caller is an admin, replying when they are not."""
    user = getattr(update.effective_user, "id", None) if update else None
    if user in (settings.admin_user_ids or []):
        return True
    if update is not None and getattr(update, "message", None) is not None:
        await safe_reply(update, "You are not authorised to review locations.")
    return False


# ---------------------------------------------------------------------------
# menu and queue
# ---------------------------------------------------------------------------


async def _show_menu(update: Any, context: ContextTypes.DEFAULT_TYPE) -> int:
    with session_scope() as session:
        counts = locations.summarize(session)
    text = (
        "📍 <b>Locations</b>\n\n"
        f"Total: {counts['total']}\n"
        f"Awaiting review: {counts['pending']}\n"
        f"Verified: {counts['verified']}\n"
        f"Rejected: {counts['rejected']}"
    )
    await safe_reply(update, text, locations_menu_keyboard(), parse_mode="HTML")
    return int(LocationAdminStates.LIST)


async def _show_queue(
    update: Any,
    context: ContextTypes.DEFAULT_TYPE,
    page: int = 0,
) -> int:
    """One page of the pending queue, oldest first."""
    with session_scope() as session:
        queue = locations.pending_locations(
            session, limit=LOCATIONS_PER_PAGE, offset=max(0, page) * LOCATIONS_PER_PAGE
        )
        waiting = locations.count_pending(session)

    total_pages = max(1, (waiting + LOCATIONS_PER_PAGE - 1) // LOCATIONS_PER_PAGE)
    if not queue:
        text = "✅ <b>Nothing to review.</b>\n\nEvery submitted location has been handled."
    else:
        text = (
            f"⏳ <b>Locations awaiting review</b> ({waiting})\n\n"
            "These were typed by hand and are <b>not</b> verified places yet."
        )
    await safe_reply(
        update,
        text,
        location_list_keyboard(
            queue, page=page, total_pages=total_pages, pending=waiting
        ),
        parse_mode="HTML",
    )
    return int(LocationAdminStates.LIST)


# ---------------------------------------------------------------------------
# detail
# ---------------------------------------------------------------------------


async def _show_detail(
    update: Any, context: ContextTypes.DEFAULT_TYPE, location_id: int
) -> int:
    with session_scope() as session:
        location = locations.get_location(session, location_id)
        if location is None:
            await safe_reply(update, "That location no longer exists.")
            return await _show_queue(update, context)
        text = _detail_text(location)

    context.user_data[_KEY_LOCATION] = {"location_id": location_id}
    await safe_reply(update, text, location_detail_keyboard(location_id), parse_mode="HTML")
    return int(LocationAdminStates.DETAIL)


def _detail_text(location: Any) -> str:
    """Everything an admin needs to judge the submission, original text included.

    The typed spelling is shown verbatim because it is the evidence: an admin
    deciding whether "Goro Sefer" is a real place needs to see what was typed,
    not what the normaliser made of it.
    """
    lines = [
        f"📍 <b>{location.name}</b>",
        "",
        f"<b>City:</b> {location.city}",
        f"<b>Sub-city / district:</b> {location.sub_city or '—'}",
        f"<b>Landmark:</b> {location.landmark or '—'}",
        "",
        f"<b>Source:</b> {location.provider}",
        f"<b>Verification:</b> {location.verification_status}",
        f"<b>Search key:</b> <code>{location.normalized_name}</code>",
    ]
    if location.has_coordinates:
        lines.append(
            f"<b>Coordinates:</b> {location.latitude:.5f}, {location.longitude:.5f}"
        )
    else:
        lines.append(
            "<b>Coordinates:</b> none — proximity will be reported uncertain"
        )
    if location.is_merged:
        lines.append(f"<b>Merged into:</b> location #{location.merged_into_id}")
    if location.aliases:
        lines.append(f"<b>Aliases:</b> {', '.join(a.alias for a in location.aliases)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------


async def callback_handler(
    update: Any, context: ContextTypes.DEFAULT_TYPE
) -> int | None:
    """Every button in the review flow.

    Returns the state to stay in, or ``None`` when the callback belongs to
    another module — which is how the admin console keeps working alongside
    this one.
    """
    query = update.callback_query
    if query is None:
        return None
    data = query.data or ""
    if not _is_ours(data):
        return None

    # Guarded here rather than only at ``/locations``: a callback is a request
    # like any other, and a review action must not be reachable by anybody who
    # did not get in through the command.
    if not await require_admin(update):
        await answer(update, "You are not authorised to review locations.", show_alert=True)
        return ConversationHandler.END
    await answer(update)

    if data == CB_ADMIN_MENU:
        return await _show_menu(update, context)
    if data == CB_ADMIN_LOCATIONS:
        return await _show_queue(update, context)
    if data == "admin:loc:find":
        return await _prompt_find(update, context)
    if data == CB_LOC_UNLINKED:
        return await _show_unlinked(update, context)
    if data.startswith(CB_LOC_LINK):
        return await _link_row(update, context, _int_tail(data))
    if data.startswith(CB_LOC_PAGE):
        return await _show_queue(update, context, page=_int_tail(data))
    if data.startswith(CB_LOC_DETAIL):
        return await _show_detail(update, context, _int_tail(data))
    if data.startswith(CB_LOC_APPROVE):
        return await _approve(update, context, _int_tail(data))
    if data.startswith(CB_LOC_REJECT_CONFIRM):
        return await _reject(update, context, _int_tail(data))
    if data.startswith(CB_LOC_REJECT):
        return await _prompt_reason(update, context, _int_tail(data))
    if data.startswith(CB_LOC_MERGE_DO):
        return await _merge(update, context, data)
    if data.startswith(CB_LOC_MERGE):
        return await _prompt_merge(update, context, _int_tail(data))
    if data.startswith(CB_LOC_ALIAS):
        return await _prompt_alias(update, context, _int_tail(data))
    if data.startswith(CB_LOC_COORDS):
        return await _prompt_coords(update, context, _int_tail(data))
    return int(LocationAdminStates.LIST)


def _is_ours(data: str) -> bool:
    """Whether a callback belongs to this flow.

    ``admin:loc:`` is the prefix the whole module works in, but a bare
    ``admin:locations`` (the menu button) and ``admin:loc:find`` (the search
    prompt) have no trailing id, so they are named rather than matched on
    shape.
    """
    return data in (
        CB_ADMIN_MENU,
        CB_ADMIN_LOCATIONS,
        CB_LOC_UNLINKED,
        "admin:loc:find",
    ) or (data.startswith("admin:loc:") and len(data.split(":")) >= 3)


def _int_tail(data: str) -> int:
    """The trailing id of a callback, or ``0`` when it is malformed.

    A stale button that cannot be read resolves to ``0``, which no location has,
    so it is reported as missing rather than acting on something else.
    """
    tail = data.rsplit(":", 1)[-1]
    return int(tail) if tail.isdigit() else 0


# ---------------------------------------------------------------------------
# actions
# ---------------------------------------------------------------------------


async def _approve(update: Any, context: ContextTypes.DEFAULT_TYPE, location_id: int) -> int:
    try:
        with session_scope() as session:
            location = locations.approve_location(session, location_id)
            name = location.name
    except LocationError as exc:
        await safe_reply(update, f"⚠️ {exc.user_message}")
        return int(LocationAdminStates.LIST)
    await safe_reply(
        update,
        f"✅ <b>{name}</b> is now a verified location.",
        location_detail_keyboard(location_id),
        parse_mode="HTML",
    )
    return int(LocationAdminStates.DETAIL)


async def _prompt_reason(update: Any, context: ContextTypes.DEFAULT_TYPE, location_id: int) -> int:
    """Ask why before rejecting, so the submitter can be told."""
    context.user_data[_KEY_LOCATION] = {"location_id": location_id, "rejecting": True}
    await safe_reply(
        update,
        "✖️ <b>Why is this location invalid?</b>\n\n"
        "Your message is kept for the admin record. Send <code>-</code> to "
        "reject with no reason.",
        location_reject_reason_keyboard(location_id),
        parse_mode="HTML",
    )
    return int(LocationAdminStates.REASON_INPUT)


async def _reason_handler(update: Any, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    payload = context.user_data.get(_KEY_LOCATION) or {}
    location_id = payload.get("location_id") or 0
    if not location_id:
        await safe_reply(update, "No rejection is pending.")
        return await _show_menu(update, context)
    return await _reject(update, context, location_id, reason=text)


async def _reject(
    update: Any,
    context: ContextTypes.DEFAULT_TYPE,
    location_id: int,
    reason: str = "",
) -> int:
    cleaned = (reason or "").strip()
    if cleaned in ("-", ""):
        reason = ""
    try:
        with session_scope() as session:
            location = locations.reject_location(session, location_id, reason or None)
            name = location.name
    except LocationError as exc:
        await safe_reply(update, f"⚠️ {exc.user_message}")
        return int(LocationAdminStates.LIST)
    await safe_reply(
        update,
        f"❌ <b>{name}</b> is rejected. It stays linked to any existing job or "
        "tutor so nothing breaks.",
        location_detail_keyboard(location_id),
        parse_mode="HTML",
    )
    return int(LocationAdminStates.DETAIL)


async def _prompt_merge(update: Any, context: ContextTypes.DEFAULT_TYPE, location_id: int) -> int:
    """Offer the candidates this location could be merged into."""
    with session_scope() as session:
        source = locations.get_location(session, location_id)
        if source is None:
            await safe_reply(update, "That location no longer exists.")
            return await _show_queue(update, context)
        targets = locations.suggest_merge_targets(session, source)
        text = (
            f"🔀 <b>Merge {source.name}</b>\n\n"
            "Pick the location it should become. Tutors and jobs already linked "
            "to it are moved across, and its spellings are kept as aliases."
        )
        keyboard = location_merge_keyboard(source, targets)
    await safe_reply(update, text, keyboard, parse_mode="HTML")
    return int(LocationAdminStates.MERGE_PICK)


async def _merge(update: Any, context: ContextTypes.DEFAULT_TYPE, data: str) -> int:
    """``admin:loc:mergeinto:<source>:<target>`` — the merge itself."""
    tail = data[len(CB_LOC_MERGE_DO) :] if data.startswith(CB_LOC_MERGE_DO) else data
    parts = tail.split(":")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        await safe_reply(update, "That merge request is out of date.")
        return int(LocationAdminStates.LIST)
    source_id, target_id = int(parts[0]), int(parts[1])

    try:
        with session_scope() as session:
            survivor = locations.merge_locations(session, source_id, target_id)
            name = survivor.name
            survivor_id = survivor.id
    except LocationError as exc:
        await safe_reply(update, f"⚠️ {exc.user_message}")
        return await _show_queue(update, context)

    await safe_reply(
        update,
        f"🔀 Merged into <b>{name}</b>. Tutor and job links were moved across, "
        "and the merged location is kept as a tombstone.",
        location_detail_keyboard(survivor_id),
        parse_mode="HTML",
    )
    return int(LocationAdminStates.DETAIL)


async def _prompt_alias(update: Any, context: ContextTypes.DEFAULT_TYPE, location_id: int) -> int:
    context.user_data[_KEY_LOCATION] = {"location_id": location_id}
    await safe_reply(
        update,
        "🔤 <b>Send the alternative spelling</b> to add.\n\n"
        "A tutor or parent who types this next time will find the same location.",
        parse_mode="HTML",
    )
    return int(LocationAdminStates.ALIAS_INPUT)


async def _alias_handler(update: Any, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    location_id = (context.user_data.get(_KEY_LOCATION) or {}).get("location_id") or 0
    try:
        with session_scope() as session:
            locations.add_alias(session, location_id, text)
            detail = _detail_text(locations.get_location(session, location_id))
    except LocationError as exc:
        await safe_reply(update, f"⚠️ {exc.user_message}", parse_mode="HTML")
        return int(LocationAdminStates.ALIAS_INPUT)
    await safe_reply(
        update,
        f"🔤 Added <b>{text}</b> to that location.\n\n{detail}",
        location_detail_keyboard(location_id),
        parse_mode="HTML",
    )
    return int(LocationAdminStates.DETAIL)


async def _prompt_coords(update: Any, context: ContextTypes.DEFAULT_TYPE, location_id: int) -> int:
    context.user_data[_KEY_LOCATION] = {"location_id": location_id}
    await safe_reply(
        update,
        "📍 <b>Send the coordinates</b> as <code>latitude, longitude</code>.\n\n"
        "Only add a position you can confirm — a wrong one misleads every match "
        "that uses it. Anything outside Ethiopia is refused.",
        parse_mode="HTML",
    )
    return int(LocationAdminStates.COORDS_INPUT)


async def _coords_handler(update: Any, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    location_id = (context.user_data.get(_KEY_LOCATION) or {}).get("location_id") or 0
    try:
        latitude, longitude = locations.parse_coordinates(text)
        with session_scope() as session:
            locations.set_coordinates(session, location_id, latitude, longitude)
    except LocationError as exc:
        await safe_reply(update, f"⚠️ {exc.user_message}", parse_mode="HTML")
        return int(LocationAdminStates.COORDS_INPUT)
    await safe_reply(
        update,
        f"📍 Coordinates set to {latitude:.5f}, {longitude:.5f}.",
        location_detail_keyboard(location_id),
        parse_mode="HTML",
    )
    return int(LocationAdminStates.DETAIL)


async def _prompt_find(update: Any, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Search the stored locations, for finding a merge target by hand."""
    await safe_reply(
        update,
        "🔎 <b>Send part of a location name</b> to find it.",
        parse_mode="HTML",
    )
    return int(LocationAdminStates.MERGE_PICK)


async def _show_unlinked(update: Any, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Tutor location rows that still point at no location record.

    This is the work list behind "normalise existing locations": a tutor whose
    text matched nothing keeps that row untouched and appears here, so an admin
    links it rather than the platform guessing. Every button opens a location,
    never a tutor, so nothing private is displayed.
    """
    with session_scope() as session:
        rows = locations.unlinked_tutor_rows(session)
        text = (
            "\u26a0\ufe0f <b>Tutor areas not matched to a location</b> "
            f"({len(rows)})\n\n"
            "These were saved before locations became records. Tap one to "
            "review the location it should become \u2014 nothing is changed "
            "unless an admin does it."
        )
        keyboard = location_unlinked_keyboard(rows)
    await safe_reply(update, text, keyboard, parse_mode="HTML")
    return int(LocationAdminStates.LIST)


async def _link_row(update: Any, context: ContextTypes.DEFAULT_TYPE, row_id: int) -> int:
    """Open the tutor row's most likely location for review.

    The row's own text is used to search, never the tutor's identity, so what is
    shown could not identify somebody. An empty search means an admin links by
    hand through the merge screen.
    """
    from app.models import TutorLocation

    with session_scope() as session:
        row = session.get(TutorLocation, row_id) if row_id else None
        if row is None:
            await safe_reply(update, "That area is no longer stored.")
            return int(LocationAdminStates.LIST)
        candidates = locations.search_locations(session, row.location, limit=10)
        text = (
            f"\u26a0\ufe0f <b>{row.location}</b>\n\n"
            "Pick the location this area should become. The tutor's own "
            "spelling stays on their profile either way."
        )

    buttons = [
        [InlineKeyboardButton(f"\U0001f4cd {item.name}", callback_data=f"{CB_LOC_DETAIL}{item.id}")]
        for item in candidates
    ] or [[InlineKeyboardButton("\u23f3 No match \u2014 search by hand", callback_data="admin:loc:find")]]
    buttons.append([InlineKeyboardButton("\u2b05\ufe0f Back", callback_data=CB_LOC_UNLINKED)])
    await safe_reply(
        update, text, InlineKeyboardMarkup(buttons), parse_mode="HTML"
    )
    return int(LocationAdminStates.LIST)
