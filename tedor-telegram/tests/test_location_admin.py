"""The location review console.

The behaviour worth pinning is the review itself: that a location typed by hand
starts unverified, that an admin can approve or reject it, that a merge moves
every reference across, and that nobody who is not an admin can reach any of it.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bot.handlers import location_review
from app.bot.keyboards import admin as kb
from app.bot.keyboards.admin import CB_LOC_LINK, CB_LOC_UNLINKED
from app.bot.states import LocationAdminStates
from app.models import Location, Tutor, TutorLocation
from app.services import location_registry_service as locations
from tests.test_conversation_flow import (
    FakeContext,
    callback_update,
    text_update,
)

PAGE = kb.LOCATIONS_PER_PAGE

#: The telegram id every admin action is pressed from.
ADMIN = 111


class _FakeLocation:
    """A stand-in with just the fields the merge keyboard reads."""

    def __init__(self, identifier: int = 1) -> None:
        self.id = identifier
        self.name = f"Place {identifier}"


class _Acting(FakeContext):
    """A context that remembers the last thing the review action said."""

    def __init__(self) -> None:
        super().__init__()
        self.last = ""


async def _run(db: Session, callback, **kw) -> FakeContext:
    """Run one review action and return the context that captured its replies."""
    context = _Acting()
    await callback(text_update("", bot=context.bot), context, **kw)
    context.last = context.bot.sent[-1]
    return context


def _submit(db: Session, name: str, city: str = "Addis Ababa", **kw) -> Location:
    """A location as a tutor would have submitted it: manual and pending."""
    return locations.resolve_or_create_manual(db, name=name, city=city, **kw).location


def _located(db: Session, name: str) -> Location:
    return locations.resolve_by_text(db, name)


@pytest.fixture(autouse=True)
def _seeded(db: Session):
    locations.seed_registry(db)
    db.commit()
    return db


@pytest.fixture
def as_admin(monkeypatch):
    """The settings an admin is identified by."""
    from app.config import settings

    monkeypatch.setattr(settings, "admin_user_ids", [111], raising=False)
    return 111


def _tutor(db: Session) -> Tutor:
    tutor = Tutor(
        public_tutor_id="TDR-000001",
        telegram_user_id=-1200,
        name="Abebe Bekele",
        display_name="Abebe",
        phone="+251911234567",
        email="",
        country="Ethiopia",
        city="Addis Ababa",
        teaching_mode="IN_PERSON",
    )
    db.add(tutor)
    db.flush()
    return tutor


# ---------------------------------------------------------------------------
# the keyboard and the states
# ---------------------------------------------------------------------------


def test_every_review_button_fits_the_telegram_limit():
    """The queue is long, so ids travel in the callback rather than the labels."""
    keyboards = [
        kb.locations_menu_keyboard(),
        kb.location_list_keyboard([], page=0, total_pages=3, pending=12),
        kb.location_detail_keyboard(123456),
        kb.location_reject_reason_keyboard(123456),
        kb.location_merge_keyboard(_FakeLocation(), [_FakeLocation(2), _FakeLocation(3)]),
        kb.location_result_keyboard(123456),
    ]
    for keyboard in keyboards:
        for row in keyboard.inline_keyboard:
            for button in row:
                if button.callback_data is None:
                    continue
                assert len(button.callback_data.encode("utf-8")) <= 64, (
                    button.callback_data
                )


def test_the_detail_screen_offers_every_review_action():
    labels = [
        button.text
        for row in kb.location_detail_keyboard(1).inline_keyboard
        for button in row
    ]
    for wanted in ("Approve", "Reject", "Merge", "Add spelling", "coordinates"):
        assert any(wanted in label for label in labels)


def test_the_admin_menu_has_a_locations_entry():
    labels = [
        button.text
        for row in kb.admin_menu_keyboard().inline_keyboard
        for button in row
    ]
    assert any("Location" in label for label in labels)


def test_the_review_band_does_not_collide_with_another_flow():
    from app.bot.states import (
        AdminStates,
        EthiopianTutorStates,
        JobPostStates,
        StudentStates,
        SupportStates,
        TutorStates,
    )

    bands = [
        (min(TutorStates), max(TutorStates)),
        (min(StudentStates), max(StudentStates)),
        (min(SupportStates), max(SupportStates)),
        (min(AdminStates), max(AdminStates)),
        (min(JobPostStates), max(JobPostStates)),
        (min(EthiopianTutorStates), max(EthiopianTutorStates)),
        (min(LocationAdminStates), max(LocationAdminStates)),
    ]
    for index, left in enumerate(bands):
        for right in bands[index + 1 :]:
            assert left[1] < right[0] or right[1] < left[0], (left, right)


# ---------------------------------------------------------------------------
# the queue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_only_manual_locations_are_queued(api_env, db, _seeded, as_admin):
    assert "Nothing to review" in (await _run(db, location_review._show_queue)).last


@pytest.mark.asyncio
async def test_a_submitted_location_appears_in_the_queue(api_env, db, _seeded, as_admin):
    _submit(db, "Woreda 12")
    db.commit()
    assert "awaiting review" in (await _run(db, location_review._show_queue)).last


@pytest.mark.asyncio
async def test_an_approved_location_leaves_the_queue(api_env, db, _seeded, as_admin):
    _submit(db, "Woreda 12")
    db.commit()
    locations.approve_location(db, _located(db, "Woreda 12").id)
    db.commit()
    assert "Nothing to review" in (await _run(db, location_review._show_queue)).last


@pytest.mark.asyncio
async def test_the_queue_pages_in_chunks(api_env, db, _seeded, as_admin):
    for index in range(PAGE + 3):
        _submit(db, f"Place {index}")
    db.commit()
    # The names live on the buttons; the message carries the count.
    assert "(10)" in (await _run(db, location_review._show_queue, page=0)).last

    context = _Acting()
    state = await location_review.callback_handler(
        callback_update("admin:loc:page:1", bot=context.bot, user_id=ADMIN),
        context,
    )
    assert state == int(LocationAdminStates.LIST)
    # Paging returns the same count message, now on the later page.
    assert "(10)" in context.bot.sent[-1]


@pytest.mark.asyncio
async def test_paging_past_the_end_returns_an_empty_queue(api_env, db, _seeded, as_admin):
    _submit(db, "Woreda 12")
    db.commit()
    assert "Nothing to review" in (
        await _run(db, location_review._show_queue, page=5)
    ).last


# ---------------------------------------------------------------------------
# detail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_detail_shows_what_was_typed_and_its_status(
    api_env, db, _seeded, as_admin
):
    location = locations.resolve_or_create_manual(
        db,
        name="Woreda 12, local name Goro Sefer",
        city="Addis Ababa",
        sub_city="Woreda 12",
        landmark="near the local market",
    ).location
    text = (await _run(
        db, location_review._show_detail, location_id=location.id
    )).last
    for wanted in ("Goro Sefer", "Addis Ababa", "Woreda 12", "local market", "PENDING"):
        assert wanted in text


@pytest.mark.asyncio
async def test_a_location_without_coordinates_says_so(api_env, db, _seeded, as_admin):
    _submit(db, "Woreda 12")
    assert "proximity will be reported uncertain" in (
        await _run(
            db, location_review._show_detail, location_id=_located(db, "Woreda 12").id
        )
    ).last


@pytest.mark.asyncio
async def test_an_unknown_id_reports_itself_missing(api_env, db, _seeded, as_admin):
    context = _Acting()
    state = await location_review._show_detail(
        text_update("", bot=context.bot), context, 999999
    )
    assert state == int(LocationAdminStates.LIST)
    # The missing-row message comes first, before the queue it falls back to.
    assert "no longer exists" in context.bot.sent[0]


# ---------------------------------------------------------------------------
# approve and reject
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approving_marks_it_verified(api_env, db, _seeded, as_admin):
    _submit(db, "Woreda 12")
    location = _located(db, "Woreda 12")
    context = _Acting()
    await location_review.callback_handler(
        callback_update(f"admin:loc:approve:{location.id}", bot=context.bot, user_id=ADMIN), context
    )
    db.expire_all()
    assert locations.get_location(db, location.id).is_verified


@pytest.mark.asyncio
async def test_rejecting_keeps_it_usable(api_env, db, _seeded, as_admin):
    """A job already pointing at a rejected place must still resolve."""
    _submit(db, "Woreda 12")
    location = _located(db, "Woreda 12")
    # One context throughout: the reason prompt stashes the location id on it,
    # and the reply has to land on the same one.
    context = _Acting()
    await location_review._prompt_reason(
        text_update("", bot=context.bot), context, location_id=location.id
    )
    await location_review._reason_handler(
        text_update("not a real place", bot=context.bot), context
    )
    db.expire_all()
    assert locations.get_location(db, location.id).is_rejected
    assert locations.resolve_by_text(db, "Woreda 12") is not None


@pytest.mark.asyncio
async def test_a_dash_reason_rejects_without_one(api_env, db, _seeded, as_admin):
    _submit(db, "Woreda 12")
    location = _located(db, "Woreda 12")
    context = _Acting()
    await location_review._prompt_reason(
        text_update("", bot=context.bot), context, location_id=location.id
    )
    await location_review._reason_handler(
        text_update("-", bot=context.bot), context
    )
    db.expire_all()
    assert locations.get_location(db, location.id).is_rejected


# ---------------------------------------------------------------------------
# aliases and coordinates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adding_a_spelling_makes_the_next_search_find_it(
    api_env, db, _seeded, as_admin
):
    _submit(db, "Woreda 12")
    location = _located(db, "Woreda 12")
    context = _Acting()
    await location_review._prompt_alias(
        text_update("", bot=context.bot), context, location_id=location.id
    )
    await location_review._alias_handler(
        text_update("Goro Sefer", bot=context.bot), context
    )
    db.expire_all()
    assert locations.resolve_by_text(db, "Goro Sefer").id == location.id


@pytest.mark.asyncio
async def test_coordinates_outside_ethiopia_are_refused(api_env, db, _seeded, as_admin):
    _submit(db, "Woreda 12")
    location = _located(db, "Woreda 12")
    context = _Acting()
    await location_review._prompt_coords(
        text_update("", bot=context.bot), context, location_id=location.id
    )
    await location_review._coords_handler(
        text_update("51.5, -0.1", bot=context.bot), context
    )
    assert "not in Ethiopia" in context.bot.sent[-1]


@pytest.mark.asyncio
async def test_a_confirmed_position_is_stored(api_env, db, _seeded, as_admin):
    _submit(db, "Woreda 12")
    location = _located(db, "Woreda 12")
    context = _Acting()
    await location_review._prompt_coords(
        text_update("", bot=context.bot), context, location_id=location.id
    )
    await location_review._coords_handler(
        text_update("9.03, 38.75", bot=context.bot), context
    )
    db.expire_all()
    assert locations.get_location(db, location.id).has_coordinates


# ---------------------------------------------------------------------------
# merging, through the console
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_merging_moves_the_tutor_link(api_env, db, _seeded, as_admin):
    keeper = locations.resolve_or_create_manual(
        db, name="Goro Sefer Market", city="Addis Ababa"
    ).location
    dup = locations.resolve_or_create_manual(
        db, name="Woreda 12 corner", city="Addis Ababa"
    ).location
    tutor = _tutor(db)
    db.add(
        TutorLocation(
            tutor_id=tutor.id,
            location=dup.name,
            normalized_location=dup.normalized_name,
            location_id=dup.id,
        )
    )
    db.commit()

    context = _Acting()
    await location_review.callback_handler(
        callback_update(
            "admin:loc:mergeinto:%d:%d" % (dup.id, keeper.id),
            bot=context.bot,
            user_id=ADMIN,
        ),
        context,
    )
    db.expire_all()

    rows = list(
        db.scalars(select(TutorLocation).where(TutorLocation.tutor_id == tutor.id))
    )
    assert len(rows) == 1
    assert rows[0].location_id == keeper.id
    # What the tutor typed is preserved even though the row changed hands.
    assert rows[0].location == dup.name


@pytest.mark.asyncio
async def test_merging_through_the_console_reports_what_happened(
    api_env, db, _seeded, as_admin
):
    keeper = locations.resolve_or_create_manual(
        db, name="Goro Sefer Market", city="Addis Ababa"
    ).location
    dup = locations.resolve_or_create_manual(
        db, name="Woreda 12 corner", city="Addis Ababa"
    ).location
    db.commit()
    context = _Acting()
    await location_review.callback_handler(
        callback_update(
            "admin:loc:mergeinto:%d:%d" % (dup.id, keeper.id),
            bot=context.bot,
            user_id=ADMIN,
        ),
        context,
    )
    assert "Merged" in context.bot.sent[-1]


@pytest.mark.asyncio
async def test_a_stale_merge_button_is_reported_not_applied(
    api_env, db, _seeded, as_admin
):
    keeper = locations.resolve_or_create_manual(
        db, name="Goro Sefer Market", city="Addis Ababa"
    ).location
    db.commit()
    context = _Acting()
    await location_review.callback_handler(
        callback_update(
            "admin:loc:mergeinto:%d:%d" % (keeper.id, keeper.id),
            bot=context.bot,
            user_id=ADMIN,
        ),
        context,
    )
    assert any("cannot be merged" in text for text in context.bot.sent)


# ---------------------------------------------------------------------------
# authorization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_non_admin_cannot_review(api_env, db, _seeded, monkeypatch):
    """A tutor must never be able to mark their own location verified."""
    from app.config import settings

    monkeypatch.setattr(settings, "admin_user_ids", [999], raising=False)
    _submit(db, "Woreda 12")
    location = _located(db, "Woreda 12")

    context = _Acting()
    await location_review.callback_handler(
        callback_update(f"admin:loc:approve:{location.id}", bot=context.bot, user_id=ADMIN),
        context,
    )
    db.expire_all()
    assert not locations.get_location(db, location.id).is_verified


@pytest.mark.asyncio
async def test_a_callback_this_flow_does_not_own_is_left_alone(api_env, db, _seeded):
    """The admin console keeps working alongside the review queue."""
    context = _Acting()
    result = await location_review.callback_handler(
        callback_update("admin:tutors", bot=context.bot), context
    )
    assert result is None




# ---------------------------------------------------------------------------
# unmatched tutor areas (specification section 7)
# ---------------------------------------------------------------------------


def _stored_tutor(db: Session, telegram_id: int = -1250) -> Tutor:
    tutor = _tutor(db)
    tutor.telegram_user_id = telegram_id
    db.commit()
    return tutor


def _button_labels(context: _Acting) -> list[str]:
    """The labels on the keyboard the last reply carried."""
    if not context.bot.markups:
        return []
    markup = context.bot.markups[-1][2]
    if markup is None:
        return []
    return [button.text for row in markup.inline_keyboard for button in row]


def _area_row(db: Session, tutor: Tutor, text: str) -> TutorLocation:
    """A tutoring area as an imported tutor had it: text, no location."""
    row = TutorLocation(
        tutor_id=tutor.id,
        location=text,
        normalized_location=locations.normalize_name(text) or text.casefold(),
        location_id=None,
        source="IMPORTED",
    )
    db.add(row)
    db.commit()
    return row


@pytest.mark.asyncio
async def test_an_area_that_matches_nothing_is_listed(api_env, db, _seeded, as_admin):
    """An imported tutor's area nobody recognises is a work item, not a guess."""
    tutor = _stored_tutor(db)
    _area_row(db, tutor, "Somewhere Unheard Of")

    context = _Acting()
    state = await location_review.callback_handler(
        callback_update(CB_LOC_UNLINKED, bot=context.bot, user_id=ADMIN), context
    )
    assert state == int(LocationAdminStates.LIST)
    assert "not matched" in context.bot.sent[-1]
    # The area itself is a button, never a sentence naming the tutor.
    assert "Somewhere Unheard Of" in " ".join(_button_labels(context))


@pytest.mark.asyncio
async def test_a_linked_area_is_not_a_work_item(api_env, db, _seeded, as_admin):
    tutor = _stored_tutor(db)
    location = locations.resolve_or_create_manual(
        db, name="Goro Sefer", city="Addis Ababa"
    ).location
    db.add(
        TutorLocation(
            tutor_id=tutor.id,
            location=location.name,
            normalized_location=location.normalized_name,
            location_id=location.id,
            source="SEARCH",
        )
    )
    db.commit()

    context = _Acting()
    await location_review.callback_handler(
        callback_update(CB_LOC_UNLINKED, bot=context.bot, user_id=ADMIN), context
    )
    assert "not matched" in context.bot.sent[-1]
    assert "(0)" in context.bot.sent[-1]
    assert _button_labels(context) == ["⬅️ Admin Menu"]


@pytest.mark.asyncio
async def test_the_list_never_names_the_tutor(
    db, _seeded, as_admin
):
    """The work item is the area, not the person who typed it."""
    tutor = _stored_tutor(db)
    tutor.name = "Abebe Bekele"
    _area_row(db, tutor, "Somewhere Unheard Of")
    db.commit()

    context = _Acting()
    await location_review.callback_handler(
        callback_update(CB_LOC_UNLINKED, bot=context.bot, user_id=ADMIN), context
    )
    assert "Abebe" not in context.bot.sent[-1]


@pytest.mark.asyncio
async def test_tapping_an_unmatched_area_offers_locations(api_env, db, _seeded, as_admin):
    tutor = _stored_tutor(db)
    row = _area_row(db, tutor, "Goro Sefer")
    locations.resolve_or_create_manual(db, name="Goro Sefer", city="Addis Ababa")
    db.commit()

    context = _Acting()
    await location_review.callback_handler(
        callback_update(f"{CB_LOC_LINK}{row.id}", bot=context.bot, user_id=ADMIN),
        context,
    )
    # A candidate location is offered, and the tutor's text is shown so the
    # admin judges the match rather than the person.
    assert "Goro Sefer" in context.bot.sent[-1]


@pytest.mark.asyncio
async def test_a_non_admin_cannot_see_the_unmatched_list(api_env, db, _seeded, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "admin_user_ids", [999], raising=False)
    tutor = _stored_tutor(db)
    _area_row(db, tutor, "Somewhere Unheard Of")

    context = _Acting()
    await location_review.callback_handler(
        callback_update(CB_LOC_UNLINKED, bot=context.bot, user_id=ADMIN), context
    )
    assert "Somewhere Unheard Of" not in " ".join(_button_labels(context))
