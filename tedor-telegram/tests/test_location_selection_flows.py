"""Selecting locations as a tutor, and as an administrator posting a job.

Both flows must resolve to the same verified directory, must never accept
invented coordinates, and must leave the existing apply/publish behaviour
alone.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models import TutorLocation
from app.services import job_post_service, location_registry_service as locations
from tests.factories import job_post_payload


@pytest.fixture(autouse=True)
def _seeded(db):
    locations.seed_registry(db)
    db.commit()
    return db


def canonical(db, name):
    location = locations.resolve_or_create_manual(
        db, name=name, city="Addis Ababa"
    ).location
    db.commit()
    return location


# ---------------------------------------------------------------------------
# searching the directory
# ---------------------------------------------------------------------------


def test_searching_by_the_standardised_name_finds_it(db):
    assert [row.name for row in locations.search_locations(db, "bole")] == ["Bole"]


def test_searching_is_case_insensitive(db):
    assert locations.search_locations(db, "BOLE")
    assert locations.search_locations(db, "BoLe")


def test_searching_by_an_alias_finds_the_place(db):
    """The alias is the point: a tutor types what they call it locally."""
    location = canonical(db, "Lideta")
    locations.add_alias(db, location, "Kebereda")
    db.commit()
    assert location.name in [
        row.name for row in locations.search_locations(db, "Kebereda")
    ]


def test_searching_by_a_partial_name_finds_several(db):
    assert len(locations.search_locations(db, "a")) > 1


def test_searching_by_the_channel_code_finds_the_place(db):
    """An administrator pastes the code straight from the channel post."""
    location = canonical(db, "Koye")
    location.location_code = "AA-0001"
    db.commit()
    assert location.name in [
        row.name for row in locations.search_locations(db, "aa-0001")
    ]


def test_searching_for_nothing_returns_nothing(db):
    assert locations.search_locations(db, "   ") == []


def test_an_unknown_name_resolves_to_nothing_rather_than_a_guess(db):
    assert locations.resolve_by_text(db, "Nowhere At All") is None


# ---------------------------------------------------------------------------
# a tutor may pick several areas and take one back
# ---------------------------------------------------------------------------


def _session():
    from app.bot.handlers import location_selector

    return location_selector.LocationSession(SimpleNamespace(user_data={}))


def _pick(session, name, location_id):
    from app.bot.handlers import location_selector

    session.pick(location_selector.Selection(name=name, location_id=location_id))


def test_a_tutor_can_select_several_areas(db):
    bole = canonical(db, "Bole")
    cmc = canonical(db, "CMC")
    session = _session()
    _pick(session, "Bole", bole.id)
    _pick(session, "CMC", cmc.id)

    payload = session.payload()
    assert payload["location_ids"] == [bole.id, cmc.id]
    assert "Bole" in payload["locations"] and "CMC" in payload["locations"]


def test_selecting_the_same_area_twice_does_not_duplicate_it(db):
    bole = canonical(db, "Bole")
    session = _session()
    _pick(session, "Bole", bole.id)
    _pick(session, "Bole", bole.id)
    assert session.payload()["location_ids"] == [bole.id]


def test_a_tutor_can_remove_an_area_they_added_by_mistake(db):
    bole = canonical(db, "Bole")
    cmc = canonical(db, "CMC")
    session = _session()
    _pick(session, "Bole", bole.id)
    _pick(session, "CMC", cmc.id)
    session.remove(0)
    assert session.payload()["location_ids"] == [cmc.id]


def test_the_remove_button_is_actually_rendered(db):
    """The removal handler existed but no keyboard ever offered the button, so
    picking an area was irreversible for the rest of the conversation."""
    from app.bot.handlers import location_selector
    from app.bot.keyboards import location as kb

    session = _session()
    _pick(session, "Bole", 1)
    _pick(session, "CMC", 2)

    keyboard = location_selector._with_chip_rows(
        kb.empty_keyboard(show_done=True), session
    )
    callbacks = [b.callback_data for row in keyboard.inline_keyboard for b in row]
    assert f"{kb.CB_REMOVE}0" in callbacks
    assert f"{kb.CB_REMOVE}1" in callbacks


def test_no_remove_buttons_are_added_when_nothing_is_selected(db):
    from app.bot.handlers import location_selector
    from app.bot.keyboards import location as kb

    keyboard = location_selector._with_chip_rows(
        kb.empty_keyboard(show_done=False), _session()
    )
    callbacks = [b.callback_data for row in keyboard.inline_keyboard for b in row]
    assert not any(str(value).startswith(kb.CB_REMOVE) for value in callbacks)


# ---------------------------------------------------------------------------
# a tutor's areas do not gain coordinates on their own
# ---------------------------------------------------------------------------


def test_a_free_text_area_stays_unresolved(db):
    """A tutor's typed area is evidence, not a coordinate source: it stays
    without a ``location_id`` until something resolves it, so matching reports
    the proximity as unknown rather than assuming a position."""
    row = TutorLocation(tutor_id=1, location="Bole", normalized_location="bole")
    assert row.location_id is None
    assert not hasattr(row, "latitude"), "a tutor-area row never holds coordinates"


# ---------------------------------------------------------------------------
# the administrator's job location
# ---------------------------------------------------------------------------


def test_a_job_stores_the_selected_location_id(db):
    location = canonical(db, "Bole")
    post = job_post_service.create_job_post(
        db, {**job_post_payload(1), "location_id": location.id}
    )
    assert post.location_id == location.id


def test_a_job_without_a_resolved_location_has_no_id(db):
    post = job_post_service.create_job_post(db, job_post_payload(1))
    assert post.location_id is None


def test_the_job_template_still_renders_the_location(db):
    post = job_post_service.create_job_post(
        db, job_post_payload(1, location="Bole")
    )
    assert "Bole" in post.generated_text
    assert "#Bole" in post.generated_text


def test_a_quick_picked_location_resolves_to_a_directory_row(db):
    """The quick-pick buttons must not bypass the directory."""
    from app.bot.handlers import job_post as handler

    link = handler._resolve_picked_location("Bole")
    assert link["ids"], "Bole is in the curated registry and must resolve"
    assert link["unresolved"] is False


def test_an_unknown_quick_pick_resolves_to_nothing(db):
    from app.bot.handlers import job_post as handler

    link = handler._resolve_picked_location("Nowhere At All")
    assert link["ids"] == []
    assert link["unresolved"] is True


def test_the_preview_names_the_standardised_location(db):
    """Requirement 4: the admin sees the canonical name before publishing."""
    from app.bot.handlers import job_post as handler

    location = canonical(db, "Bole")
    context = SimpleNamespace(
        user_data={handler.LOCATION_LINK_KEY: {"ids": [location.id]}}
    )
    line = handler._location_confirmation(context)
    assert "Bole" in line
    assert "not linked" not in line


def test_the_preview_says_so_when_the_place_is_not_in_the_directory(db):
    from app.bot.handlers import job_post as handler

    context = SimpleNamespace(
        user_data={handler.LOCATION_LINK_KEY: {"ids": [], "unresolved": True}}
    )
    assert "not in the directory" in handler._location_confirmation(context).lower()


def test_only_one_location_id_is_claimed_for_a_post(db):
    """Several picks leave the job resolvable by name rather than by an
    arbitrary one of them."""
    from app.bot.handlers import job_post as handler

    context = SimpleNamespace(
        user_data={handler.LOCATION_LINK_KEY: {"ids": [4, 9]}}
    )
    assert handler._chosen_location_id(context) is None

    context = SimpleNamespace(user_data={handler.LOCATION_LINK_KEY: {"ids": [4]}})
    assert handler._chosen_location_id(context) == 4


def test_no_link_at_all_claims_no_id(db):
    from app.bot.handlers import job_post as handler

    assert handler._chosen_location_id(SimpleNamespace(user_data={})) is None