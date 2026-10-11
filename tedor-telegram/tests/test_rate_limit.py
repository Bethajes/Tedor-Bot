"""Rate limiting and the guards around it.

The limiter is in-process, so the tests drive it directly rather than through
a running server. What matters is that a caller who has used their allowance is
told to wait, that the message is one a person can read, and that a new caller
is never refused by somebody else's usage.
"""

from __future__ import annotations


import pytest

from app.services import rate_limit


@pytest.fixture(autouse=True)
def _reset():
    rate_limit.reset()
    yield
    rate_limit.reset()


def test_a_new_caller_is_allowed():
    verdict = rate_limit.allow("scope", "user-a", limit=3, window=60)
    assert verdict.allowed


def test_the_allowance_runs_out_then_recovers():
    for _ in range(3):
        assert rate_limit.allow("scope", "user-a", limit=3, window=60).allowed
    verdict = rate_limit.allow("scope", "user-a", limit=3, window=60)
    assert not verdict.allowed
    assert verdict.message


def test_one_caller_cannot_exhaust_another():
    for _ in range(3):
        rate_limit.allow("scope", "user-a", limit=3, window=60)
    assert rate_limit.allow("scope", "user-b", limit=3, window=60).allowed


def test_scopes_are_independent():
    for _ in range(3):
        rate_limit.allow("search", "user-a", limit=3, window=60)
    assert rate_limit.allow("manual", "user-a", limit=3, window=60).allowed


def test_a_denied_request_reports_how_long_to_wait():
    for _ in range(3):
        rate_limit.allow("scope", "user-a", limit=3, window=60)
    verdict = rate_limit.allow("scope", "user-a", limit=3, window=60)
    assert not verdict.allowed
    assert verdict.retry_after > 0
    assert "try again" in verdict.message.lower()


def test_a_long_wait_is_reported_in_minutes():
    for _ in range(2):
        rate_limit.allow("scope", "user-a", limit=2, window=7200)
    verdict = rate_limit.allow("scope", "user-a", limit=2, window=7200)
    assert "minute" in verdict.message


def test_a_zero_limit_denies_everything():
    """A misconfigured limit must fail closed, not let everything through."""
    verdict = rate_limit.allow("scope", "user-a", limit=0, window=60)
    assert not verdict.allowed


def test_a_zero_window_denies_everything():
    verdict = rate_limit.allow("scope", "user-a", limit=5, window=0)
    assert not verdict.allowed


def test_the_allowance_refills_after_the_window():
    now = 1000.0
    for _ in range(3):
        rate_limit.allow("scope", "user-a", limit=3, window=10, now=now)
    assert not rate_limit.allow("scope", "user-a", limit=3, window=10, now=now).allowed
    assert rate_limit.allow("scope", "user-a", limit=3, window=10, now=now + 20).allowed


def test_a_partial_wait_is_not_quite_enough():
    """Half a token refilled is still not a whole request."""
    for _ in range(3):
        rate_limit.allow("scope", "user-a", limit=3, window=60, now=0.0)
    # 10 of 60 seconds refills half of one token, which is not a request.
    assert not rate_limit.allow("scope", "user-a", limit=3, window=60, now=10.0).allowed
    # The whole window refills the whole bucket.
    assert rate_limit.allow("scope", "user-a", limit=3, window=60, now=60.0).allowed


def test_status_does_not_spend_a_token():
    for _ in range(2):
        rate_limit.allow("scope", "user-a", limit=3, window=60)
    assert rate_limit.bucket_status("scope", "user-a", limit=3, window=60).remaining == 1
    assert rate_limit.allow("scope", "user-a", limit=3, window=60).allowed


def test_a_rotating_caller_cannot_grow_the_map_without_bound():
    for index in range(rate_limit._MAX_BUCKETS + 10):
        rate_limit.allow("scope", f"user-{index}", limit=1, window=3600)
    assert len(rate_limit._buckets) <= rate_limit._MAX_BUCKETS


def test_reset_clears_only_the_named_scope():
    rate_limit.allow("one", "user-a", limit=1, window=60)
    rate_limit.allow("two", "user-a", limit=1, window=60)
    rate_limit.reset("one")
    # The reset scope starts again from a full bucket.
    assert rate_limit.allow("one", "user-a", limit=1, window=60).allowed
    # The untouched scope is still empty.
    assert not rate_limit.allow("two", "user-a", limit=1, window=60).allowed


