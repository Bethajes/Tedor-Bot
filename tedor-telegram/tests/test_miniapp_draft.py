"""Property-based tests for the Mini App draft service.

Covers:
  Property 5: Draft save-then-load round trip  (Requirements 8.3, 9.3)
  Property 6: Parent draft round trip          (Requirements 9.5)
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from hypothesis import given, settings as hyp_settings
from hypothesis import strategies as st
from sqlalchemy.orm import Session

from app.services.draft_service import (
    delete_draft,
    get_draft,
    get_draft_payload,
    save_draft,
)

# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

# Scalar JSON-safe values that can appear in a draft payload
_json_scalar = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-10_000, max_value=10_000),
    st.floats(min_value=0.0, max_value=9999.0, allow_nan=False, allow_infinity=False),
    st.text(max_size=200),
)

# A flat dict with up to 15 string keys and JSON-safe scalar values
_flat_payload = st.dictionaries(
    keys=st.text(alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd"), whitelist_characters="_"), min_size=1, max_size=40),
    values=_json_scalar,
    max_size=15,
)

# A slightly richer payload that can nest one level deep (lists of scalars)
_nested_payload = st.fixed_dictionaries(
    {},
    optional={
        "name": st.text(max_size=100),
        "age": st.integers(min_value=18, max_value=100),
        "subjects": st.lists(st.text(max_size=40), max_size=10),
        "teaching_mode": st.sampled_from(["ONLINE", "IN_PERSON", "BOTH"]),
        "english_proficiency": st.integers(min_value=1, max_value=10),
        "cgpa": st.floats(min_value=0.0, max_value=4.0, allow_nan=False),
        "notes": st.text(max_size=300),
    },
)

_tutor_payload = st.one_of(_flat_payload, _nested_payload)
_parent_payload = st.one_of(_flat_payload, st.fixed_dictionaries(
    {},
    optional={
        "parent_name": st.text(max_size=100),
        "student_name": st.text(max_size=100),
        "country": st.text(max_size=80),
        "subject": st.text(max_size=80),
        "mode": st.sampled_from(["ONLINE", "IN_PERSON", "BOTH"]),
        "budget": st.floats(min_value=0.0, max_value=100_000.0, allow_nan=False),
    },
))

_user_id = st.integers(min_value=100_000, max_value=999_999_999)


# ---------------------------------------------------------------------------
# Property 5: Draft save-then-load round trip
# ---------------------------------------------------------------------------

# Feature: tedor-mini-app, Property 5: Draft save-then-load round trip
@given(telegram_user_id=_user_id, payload=_tutor_payload)
@hyp_settings(max_examples=100, deadline=None)
def test_tutor_draft_round_trip(
    fresh_db: Any,
    telegram_user_id: int,
    payload: dict,
) -> None:
    """For any partial tutor payload, save then load returns the same values.

    Validates: Requirements 8.3, 9.3
    """
    db: Session = fresh_db()
    save_draft(db, telegram_user_id, "tutor", payload)
    db.commit()

    loaded = get_draft_payload(db, telegram_user_id, "tutor")

    assert loaded is not None, "draft must exist after save"
    assert loaded == payload, (
        f"round-trip mismatch: saved {payload!r}, got {loaded!r}"
    )


# ---------------------------------------------------------------------------
# Property 6: Parent draft round trip
# ---------------------------------------------------------------------------

# Feature: tedor-mini-app, Property 6: Parent draft round trip
@given(telegram_user_id=_user_id, payload=_parent_payload)
@hyp_settings(max_examples=100, deadline=None)
def test_parent_draft_round_trip(
    fresh_db: Any,
    telegram_user_id: int,
    payload: dict,
) -> None:
    """For any partial parent payload, save then load returns the same values.

    Validates: Requirements 9.5
    """
    db: Session = fresh_db()
    save_draft(db, telegram_user_id, "parent", payload)
    db.commit()

    loaded = get_draft_payload(db, telegram_user_id, "parent")

    assert loaded is not None, "parent draft must exist after save"
    assert loaded == payload, (
        f"round-trip mismatch: saved {payload!r}, got {loaded!r}"
    )


# ---------------------------------------------------------------------------
# Additional correctness: upsert semantics
# ---------------------------------------------------------------------------

def test_save_draft_overwrites_existing(db: Session) -> None:
    """Saving twice with the same user/type replaces the payload."""
    uid = 42_000_001
    save_draft(db, uid, "tutor", {"step": 1})
    db.commit()
    save_draft(db, uid, "tutor", {"step": 2, "name": "Alice"})
    db.commit()

    loaded = get_draft_payload(db, uid, "tutor")
    assert loaded == {"step": 2, "name": "Alice"}


def test_delete_draft_removes_row(db: Session) -> None:
    """After delete_draft, get_draft returns None."""
    uid = 42_000_002
    save_draft(db, uid, "tutor", {"step": 1})
    db.commit()
    delete_draft(db, uid, "tutor")
    db.commit()

    assert get_draft(db, uid, "tutor") is None


def test_tutor_and_parent_drafts_are_independent(db: Session) -> None:
    """Tutor and parent drafts for the same user are stored separately."""
    uid = 42_000_003
    save_draft(db, uid, "tutor", {"form": "tutor"})
    save_draft(db, uid, "parent", {"form": "parent"})
    db.commit()

    assert get_draft_payload(db, uid, "tutor") == {"form": "tutor"}
    assert get_draft_payload(db, uid, "parent") == {"form": "parent"}
