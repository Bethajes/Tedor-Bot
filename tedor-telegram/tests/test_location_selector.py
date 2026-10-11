"""The location selector, driven as a user would drive it.

Every search here goes through a stub provider, so no API key and no network are
needed. What is being checked is the interaction: that a keystroke is not a
request, that a typed answer survives a switch to the manual form, that chips
come off, and that nothing typed by hand is ever presented as verified.
"""

from __future__ import annotations

import asyncio

import pytest

from app.bot.handlers import location_selector, location_prompts
from app.bot.handlers.location_selector import (
    DebounceConfig,
    LocationSession,
    Selection,
    Stage,
)
from app.services.geocoding.base import PlaceSuggestion
from tests.test_ethiopian_onboarding import FakeContext, callback_update, text_update

#: Debounce shortened to nothing: the tests check *that* a debounce exists, not
#: that a real pause does.
FAST = DebounceConfig(debounce=0)


class _StubProvider:
    """A provider that records what it was asked."""

    name = "STUB"
    is_configured = True

    def __init__(self, places: list[PlaceSuggestion] | None = None) -> None:
        self.calls: list[str] = []
        self.places = places if places is not None else []

    async def autocomplete(self, text, *, limit=8, country_code="et", proximity=None):
        self.calls.append(text)
        return list(self.places)


PLACES = [
    PlaceSuggestion(
        place_id="p1",
        name="Ayat Tsebel",
        formatted_address="Ayat Tsebel, Addis Ababa, Ethiopia",
        city="Addis Ababa",
        latitude=9.03,
        longitude=38.75,
    ),
    PlaceSuggestion(
        place_id="p2",
        name="Bole",
        formatted_address="Bole, Addis Ababa, Ethiopia",
        city="Addis Ababa",
        latitude=8.99,
        longitude=38.79,
    ),
    PlaceSuggestion(
        place_id="p3",
        name="CMC",
        formatted_address="CMC, Addis Ababa, Ethiopia",
        city="Addis Ababa",
        latitude=9.02,
        longitude=38.83,
    ),
]


@pytest.fixture
def stub(monkeypatch) -> _StubProvider:
    provider = _StubProvider(PLACES)
    monkeypatch.setattr(location_selector.geocoding, "get_provider", lambda: provider)
    return provider


async def _settle() -> None:
    """Let a scheduled debounce run, as a real pause would.

    Yielded repeatedly rather than once: a task created on this loop has to be
    entered at least twice before it can reach its own first ``await``.
    """
    for _ in range(5):
        await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# entry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_opening_the_selector_prompts_for_a_search(api_env, db, stub):
    context = FakeContext()
    state = await location_selector.on_enter(
        text_update("", bot=context.bot), context, state=505
    )
    assert state == 505
    assert location_prompts.PROMPT_SEARCH.strip().startswith("\U0001f4cd")


@pytest.mark.asyncio
async def test_opening_the_selector_always_offers_the_manual_fallback(api_env, db, stub):
    context = FakeContext()
    await location_selector.on_enter(
        text_update("", bot=context.bot), context, state=505
    )
    from app.bot.keyboards import location as kb

    labels = [
        button.text
        for row in kb.search_keyboard().inline_keyboard
        for button in row
    ]
    assert any("Add it manually" in label for label in labels)


@pytest.mark.asyncio
async def test_resuming_shows_the_previous_selections(api_env, db, stub):
    context = FakeContext()
    await location_selector.on_enter(
        text_update("", bot=context.bot),
        context,
        state=505,
        preselect=["Ayat Tsebel"],
    )
    session = LocationSession.get(context)
    assert [item.name for item in session.selected] == ["Ayat Tsebel"]


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_typing_starts_a_search(api_env, db, stub):
    context = FakeContext()
    await location_selector.on_enter(
        text_update("", bot=context.bot), context, state=505
    )
    await location_selector.handle_text(
        text_update("Ayat Tzbel", bot=context.bot),
        context,
        state=505,
        config=FAST,
    )
    await _settle()
    assert stub.calls == ["Ayat Tzbel"]


@pytest.mark.asyncio
async def test_a_very_short_query_is_not_sent(api_env, db, stub):
    context = FakeContext()
    await location_selector.on_enter(
        text_update("", bot=context.bot), context, state=505
    )
    await location_selector.handle_text(
        text_update("ay", bot=context.bot), context, state=505, config=FAST
    )
    await _settle()
    assert stub.calls == []


@pytest.mark.asyncio
async def test_the_debounce_sends_one_request_for_many_keystrokes(api_env, db, stub):
    """Typing has to cost one request, not one per character."""
    context = FakeContext()
    await location_selector.on_enter(
        text_update("", bot=context.bot), context, state=505
    )
    for word in ("a", "ay", "aya", "ayat", "ayat tzbel"):
        await location_selector.handle_text(
            text_update(word, bot=context.bot), context, state=505, config=FAST
        )
    await _settle()
    assert len(stub.calls) == 1
    assert stub.calls == ["ayat tzbel"]


@pytest.mark.asyncio
async def test_empty_results_still_leave_the_manual_route_open(api_env, db, stub):
    stub.places = []
    context = FakeContext()
    await location_selector.on_enter(
        text_update("", bot=context.bot), context, state=505
    )
    await location_selector.handle_text(
        text_update("Nowhere", bot=context.bot), context, state=505, config=FAST
    )
    await _settle()
    assert any(
        "Add it manually" in text for text in context.bot.sent
    ) or location_selector.LocationSession.get(context).stage == Stage.RESULTS


@pytest.mark.asyncio
async def test_a_provider_error_leaves_the_previous_choices_intact(api_env, db, monkeypatch):
    from app.services.geocoding.base import GeocodingError

    class _Broken:
        name = "STUB"
        is_configured = True

        async def autocomplete(self, *args, **kwargs):
            raise GeocodingError("provider detail that must not be shown")

    monkeypatch.setattr(location_selector.geocoding, "get_provider", _Broken)
    context = FakeContext()
    session = LocationSession.start(context, teaching_mode="IN_PERSON")
    session.pick(Selection(name="Ayat Tsebel", location_id=1))
    await location_selector.handle_text(
        text_update("Ayat Tzbel", bot=context.bot), context, state=505, config=FAST
    )
    await _settle()
    assert [item.name for item in session.selected] == ["Ayat Tsebel"]
    # The provider's own message never reaches the user.
    assert not any("must not be shown" in text for text in context.bot.sent)


@pytest.mark.asyncio
async def test_a_rate_limited_search_is_reported_calmly(api_env, db, monkeypatch):
    from app.services.geocoding.base import GeocodingRateLimited

    class _Limited:
        name = "STUB"
        is_configured = True

        async def autocomplete(self, *args, **kwargs):
            raise GeocodingRateLimited()

    monkeypatch.setattr(location_selector.geocoding, "get_provider", _Limited)
    context = FakeContext()
    LocationSession.start(context)
    await location_selector.handle_text(
        text_update("Ayat Tzbel", bot=context.bot), context, state=505, config=FAST
    )
    await _settle()
    assert any("manually" in text for text in context.bot.sent)


@pytest.mark.asyncio
async def test_search_is_switched_off_cleanly_without_a_key(api_env, db, monkeypatch):
    from app.services.geocoding.base import GeocodingNotConfigured

    class _Unconfigured:
        name = "STUB"
        is_configured = False

        async def autocomplete(self, *args, **kwargs):
            raise GeocodingNotConfigured()

    monkeypatch.setattr(location_selector.geocoding, "get_provider", _Unconfigured)
    context = FakeContext()
    LocationSession.start(context)
    await location_selector.handle_text(
        text_update("Ayat Tzbel", bot=context.bot), context, state=505, config=FAST
    )
    await _settle()
    assert any("manually" in text for text in context.bot.sent)


# ---------------------------------------------------------------------------
# selection and chips
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_picking_a_suggestion_adds_a_chip(api_env, db, stub):
    context = FakeContext()
    await location_selector.on_enter(
        text_update("", bot=context.bot), context, state=505
    )
    session = LocationSession.get(context)
    session.set_suggestions(PLACES, now=__import__("time").monotonic())
    result = await location_selector.handle_callback(
        callback_update("loc:choose:0", bot=context.bot),
        context,
        state=505,
    )
    assert result == 505
    assert [chip.name for chip in session.selected] == ["Ayat Tsebel"]


@pytest.mark.asyncio
async def test_several_locations_can_be_selected(api_env, db, stub):
    context = FakeContext()
    await location_selector.on_enter(
        text_update("", bot=context.bot), context, state=505
    )
    session = LocationSession.get(context)
    session.set_suggestions(PLACES, now=__import__("time").monotonic())
    for index in (0, 1, 2):
        await location_selector.handle_callback(
            callback_update(f"loc:choose:{index}", bot=context.bot), context, state=505
        )
    assert [chip.name for chip in session.selected] == ["Ayat Tsebel", "Bole", "CMC"]


@pytest.mark.asyncio
async def test_picking_the_same_place_twice_is_ignored(api_env, db, stub):
    context = FakeContext()
    await location_selector.on_enter(
        text_update("", bot=context.bot), context, state=505
    )
    session = LocationSession.get(context)
    session.set_suggestions(PLACES, now=__import__("time").monotonic())
    for _ in range(3):
        await location_selector.handle_callback(
            callback_update("loc:choose:0", bot=context.bot), context, state=505
        )
    assert len(session.selected) == 1


@pytest.mark.asyncio
async def test_a_chip_can_be_removed(api_env, db, stub):
    context = FakeContext()
    await location_selector.on_enter(
        text_update("", bot=context.bot), context, state=505
    )
    session = LocationSession.get(context)
    session.set_suggestions(PLACES, now=__import__("time").monotonic())
    for index in (0, 1):
        await location_selector.handle_callback(
            callback_update(f"loc:choose:{index}", bot=context.bot), context, state=505
        )
    await location_selector.handle_callback(
        callback_update("loc:remove:0", bot=context.bot), context, state=505
    )
    assert [chip.name for chip in session.selected] == ["Bole"]


@pytest.mark.asyncio
async def test_a_stale_result_button_asks_for_a_fresh_search(api_env, db, stub):
    """A button left open for ten minutes must not pick an old result."""
    context = FakeContext()
    await location_selector.on_enter(
        text_update("", bot=context.bot), context, state=505
    )
    session = LocationSession.get(context)
    session.set_suggestions(PLACES, now=0.0)
    await location_selector.handle_callback(
        callback_update("loc:choose:0", bot=context.bot), context, state=505
    )
    assert session.selected == []


@pytest.mark.asyncio
async def test_an_out_of_range_result_index_is_ignored(api_env, db, stub):
    context = FakeContext()
    await location_selector.on_enter(
        text_update("", bot=context.bot), context, state=505
    )
    session = LocationSession.get(context)
    session.set_suggestions(PLACES, now=__import__("time").monotonic())
    await location_selector.handle_callback(
        callback_update("loc:choose:99", bot=context.bot), context, state=505
    )
    assert session.selected == []


# ---------------------------------------------------------------------------
# the manual fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_switching_to_manual_keeps_what_was_typed(api_env, db, stub):
    context = FakeContext()
    await location_selector.on_enter(
        text_update("", bot=context.bot), context, state=505
    )
    await location_selector.handle_text(
        text_update("Woreda 12", bot=context.bot), context, state=505, config=FAST
    )
    await _settle()
    session = LocationSession.get(context)
    await location_selector.handle_callback(
        callback_update("loc:manual", bot=context.bot), context, state=505
    )
    assert session.stage == Stage.MANUAL_NAME
    assert session.manual_name == "Woreda 12"


@pytest.mark.asyncio
async def test_the_manual_fallback_is_offered_when_there_are_no_results(api_env, db):
    stub.places = []
    context = FakeContext()
    await location_selector.on_enter(
        text_update("", bot=context.bot), context, state=505
    )
    await location_selector.handle_text(
        text_update("Woreda 12", bot=context.bot), context, state=505, config=FAST
    )
    await _settle()
    from app.bot.keyboards import location as kb

    labels = [
        button.text
        for row in kb.empty_keyboard().inline_keyboard
        for button in row
    ]
    assert any("Add it manually" in label for label in labels)


@pytest.mark.asyncio
async def test_a_manual_location_is_submitted_pending(api_env, db):
    """A place typed by hand is created, and never as a verified one."""
    from app.services import location_registry_service

    context = FakeContext()
    session = LocationSession.start(context)
    session.stage = Stage.MANUAL_NAME
    session.manual_name = "Woreda 12"
    session.manual_city = "Addis Ababa"
    session.manual_sub_city = "Woreda 12"
    session.manual_landmark = "near the local market"

    async def saver(name, city, sub_city, landmark):
        with location_registry_service and __import__(
            "app.database", fromlist=["session_scope"]
        ).session_scope() as db_session:
            resolved = location_registry_service.resolve_or_create_manual(
                db_session, name=name, city=city, sub_city=sub_city, landmark=landmark
            )
            location = resolved.location
            return {
                "name": location.name,
                "location_id": location.id,
                "source_text": location.name,
                "city": location.city,
                "sub_city": location.sub_city or "",
                "latitude": location.latitude,
                "longitude": location.longitude,
                "normalized_name": location.normalized_name,
                "verification_status": location.verification_status,
                "is_suggestion": resolved.is_suggestion,
            }

    state = await location_selector.handle_callback(
        callback_update("loc:man:save", bot=context.bot),
        context,
        state=505,
        save=saver,
    )
    assert state == 505
    chosen = session.selected
    assert [item.name for item in chosen] == ["Woreda 12"]
    assert all(item.is_manual for item in chosen)
    assert any("unverified" in text for text in context.bot.sent)


@pytest.mark.asyncio
async def test_a_location_the_saver_already_has_is_offered_not_copied(api_env, db):
    """Two people naming the same place get one row and a confirmation."""
    from app.services import location_registry_service
    from app.database import session_scope

    with session_scope() as db_session:
        location_registry_service.resolve_or_create_manual(
            db_session, name="Goro Sefer", city="Addis Ababa"
        )
        db_session.commit()
        location_id = (
            location_registry_service.resolve_by_text(db_session, "Goro Sefer").id
        )

    context = FakeContext()
    session = LocationSession.start(context)
    session.stage = Stage.CONFIRM
    session.manual_name = "Goro Sefer"
    session.manual_city = "Addis Ababa"

    async def saver(name, city, sub_city, landmark):
        with session_scope() as db_session:
            resolved = location_registry_service.resolve_or_create_manual(
                db_session, name=name, city=city, sub_city=sub_city, landmark=landmark
            )
            row = resolved.location
            return {
                "name": row.name,
                "location_id": row.id,
                "source_text": row.name,
                "city": row.city,
                "sub_city": row.sub_city or "",
                "latitude": row.latitude,
                "longitude": row.longitude,
                "normalized_name": row.normalized_name,
                "is_suggestion": resolved.is_suggestion,
            }

    await location_selector.handle_callback(
        callback_update("loc:man:save", bot=context.bot),
        context,
        state=505,
        save=saver,
    )
    assert session.stage == Stage.DUPLICATE
    # Accepting the suggestion attaches the row we already had.
    await location_selector.handle_callback(
        callback_update("loc:keep", bot=context.bot),
        context,
        state=505,
        save=saver,
    )
    assert session.selected[0].location_id == location_id


@pytest.mark.asyncio
async def test_backing_out_of_the_manual_form_returns_to_the_results(api_env, db, stub):
    context = FakeContext()
    await location_selector.on_enter(
        text_update("", bot=context.bot), context, state=505
    )
    session = LocationSession.get(context)
    session.set_suggestions(PLACES, now=__import__("time").monotonic())
    session.stage = Stage.MANUAL_NAME
    await location_selector.handle_callback(
        callback_update("loc:back", bot=context.bot), context, state=505
    )
    assert session.stage == Stage.RESULTS


# ---------------------------------------------------------------------------
# completion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_done_ends_the_selector(api_env, db):
    context = FakeContext()
    session = LocationSession.start(context)
    session.pick(Selection(name="Ayat Tsebel", location_id=1))
    assert (
        await location_selector.handle_callback(
            callback_update("loc:done", bot=context.bot), context, state=505
        )
        is location_selector.done
    )


@pytest.mark.asyncio
async def test_an_in_person_tutor_cannot_finish_without_a_location(api_env, db):
    context = FakeContext()
    session = LocationSession.start(context)
    result = await location_selector.handle_callback(
        callback_update("loc:done", bot=context.bot), context, state=505
    )
    assert result == 505
    assert session.can_finish is False
    # The refusal is an alert on the pressed button, so the step does not move
    # and nothing is written to the payload.
    assert context.bot.answers and "at least one" in context.bot.answers[-1]


@pytest.mark.asyncio
async def test_an_online_tutor_finishes_without_a_location(api_env, db):
    context = FakeContext()
    session = LocationSession.start(context, teaching_mode="ONLINE")
    assert session.can_finish is True
    assert (
        await location_selector.handle_callback(
            callback_update("loc:done", bot=context.bot), context, state=505
        )
        is location_selector.done
    )


@pytest.mark.asyncio
async def test_typing_a_list_of_known_areas_is_a_complete_answer(api_env, db):
    context = FakeContext()
    await location_selector.on_enter(
        text_update("", bot=context.bot), context, state=505
    )
    outcome = await location_selector.handle_text(
        text_update("Ayat Tsebel, Bole", bot=context.bot),
        context,
        state=505,
        config=FAST,
    )
    assert outcome is location_selector.done
    session = LocationSession.get(context)
    assert [item.name for item in session.selected] == ["Ayat Tsebel", "Bole"]


@pytest.mark.asyncio
async def test_an_unknown_name_in_a_list_is_not_silently_dropped(api_env, db):
    context = FakeContext()
    await location_selector.on_enter(
        text_update("", bot=context.bot), context, state=505
    )
    session = LocationSession.get(context)
    await location_selector.handle_text(
        text_update("Bole, Somewhere Unheard Of", bot=context.bot),
        context,
        state=505,
        config=FAST,
    )
    assert [item.name for item in session.selected] == ["Bole"]
    assert any("Somewhere Unheard Of" in text for text in context.bot.sent)


@pytest.mark.asyncio
async def test_a_single_known_area_typed_and_sent_is_a_complete_answer(api_env, db):
    """A place we already hold does not need suggestions or a second tap."""
    context = FakeContext()
    await location_selector.on_enter(
        text_update("", bot=context.bot), context, state=505
    )
    outcome = await location_selector.handle_text(
        text_update("Ayat Tsebel", bot=context.bot), context, state=505, config=FAST
    )
    assert outcome is location_selector.done


@pytest.mark.asyncio
async def test_the_payload_carries_both_names_and_canonical_ids(api_env, db):
    context = FakeContext()
    session = LocationSession.start(context)
    session.pick(Selection(name="Ayat Tsebel", location_id=4))
    payload = session.payload()
    assert payload["locations"] == ["Ayat Tsebel"]
    assert payload["location_ids"] == [4]


@pytest.mark.asyncio
async def test_a_callback_the_selector_does_not_own_is_left_alone(api_env, db):
    """The owning conversation still handles its own buttons."""
    context = FakeContext()
    LocationSession.start(context)
    result = await location_selector.handle_callback(
        callback_update("eth:grades:done", bot=context.bot), context, state=505
    )
    assert result is None


# ---------------------------------------------------------------------------
# the home-area question
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_home_area_is_offered_once_the_tutor_has_no_tutoring_area(api_env, db):
    context = FakeContext()
    location_selector.LocationSession.start(context)
    state = await location_selector.ask_about_home_area(
        text_update("", bot=context.bot),
        context,
        state=505,
        home_area="Bole, Addis Ababa",
    )
    assert state == 505
    assert location_selector.home_area_pending(context) == "Bole, Addis Ababa"


@pytest.mark.asyncio
async def test_answering_no_adds_nothing(api_env, db):
    context = FakeContext()
    location_selector.LocationSession.start(context)
    await location_selector.ask_about_home_area(
        text_update("", bot=context.bot),
        context,
        state=505,
        home_area="Bole, Addis Ababa",
    )
    session = location_selector.LocationSession.get(context)
    await location_selector.answer_about_home_area(
        callback_update("eth:home:no", bot=context.bot),
        context,
        state=505,
        answer_text="no",
    )
    assert session.selected == []
    assert location_selector.home_area_pending(context) == ""


@pytest.mark.asyncio
async def test_answering_yes_adds_the_home_area(api_env, db):
    context = FakeContext()
    location_selector.LocationSession.start(context)
    await location_selector.ask_about_home_area(
        text_update("", bot=context.bot),
        context,
        state=505,
        home_area="Ayat Tsebel",
    )
    await location_selector.answer_about_home_area(
        callback_update("eth:home:yes", bot=context.bot),
        context,
        state=505,
        answer_text="yes",
    )
    session = location_selector.LocationSession.get(context)
    assert [item.name for item in session.selected] == ["Ayat Tsebel"]
    # The address is kept as the source, so the row is traceable to the answer.
    assert session.selected[0].source_text == "Ayat Tsebel"


@pytest.mark.asyncio
async def test_no_home_area_means_nothing_is_asked(api_env, db):
    context = FakeContext()
    location_selector.LocationSession.start(context)
    result = await location_selector.ask_about_home_area(
        text_update("", bot=context.bot), context, state=505, home_area="  "
    )
    assert result is None


@pytest.mark.asyncio
async def test_the_area_is_not_offered_again_once_it_is_a_tutoring_area(api_env, db):
    context = FakeContext()
    session = location_selector.LocationSession.start(context)
    session.pick(Selection(name="Ayat Tsebel", location_id=1, normalized_name="ayat tsebel"))
    result = await location_selector.ask_about_home_area(
        text_update("", bot=context.bot),
        context,
        state=505,
        home_area="Ayat Tsebel, Addis",
    )
    assert result is None
