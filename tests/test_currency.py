"""Currency routing (Property 4).

Feature: tedor-telegram-bot, Property 4: Currency routing by country
Validates: Requirements 2.4, 2.5, 6.2, 6.3
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings as hypothesis_settings, strategies as st

from app.bot.handlers.tutor import build_tutor_payload, finalise_rates
from app.enums import Currency
from app.services.currency import convert, currency_for_country, is_ethiopia
from app.services.student_service import budget_fields, create_student_request
from tests.factories import student_request_payload

PROFILE = hypothesis_settings(max_examples=100, deadline=None)

COUNTRIES = st.sampled_from(
    [
        "Ethiopia",
        "ethiopia",
        "  Ethiopia ",
        "ETHIOPIA",
        "Kenya",
        "United States",
        "Germany",
        "United Arab Emirates",
        "Other",
    ]
)

ETHIOPIAN = st.just("Ethiopia")


@given(country=COUNTRIES, rate=st.floats(min_value=10, max_value=5000))
@PROFILE
def test_property_tutor_currency_routing(country: str, rate: float) -> None:
    """Feature: tedor-telegram-bot, Property 4: Currency routing by country.

    Ethiopian tutors always end up with an ETB rate, everybody else with USD.
    """
    application = {"country": country, "usd_rate": rate, "etb_rate": None}
    finalise_rates(application)
    if is_ethiopia(country):
        assert application["etb_rate"] is not None
        assert currency_for_country(country) == Currency.ETB
    else:
        assert application["usd_rate"] == rate
        assert currency_for_country(country) == Currency.USD


@given(country=ETHIOPIAN, rate=st.floats(min_value=10, max_value=5000))
@PROFILE
def test_property_ethiopian_tutor_payload_has_etb_rate(country: str, rate: float) -> None:
    application = {
        "full_name": "A B",
        "display_name": "A",
        "phone": "+251911000000",
        "email": "a@example.com",
        "country": country,
        "city": "Addis Ababa",
        "etb_rate": None,
        "usd_rate": rate,
    }
    payload = build_tutor_payload(-1, application)
    assert payload["etb_rate"] is not None
    assert payload["etb_rate"] == pytest.approx(convert(rate, Currency.USD, Currency.ETB))


@given(country=COUNTRIES, budget=st.floats(min_value=5, max_value=9000))
@PROFILE
def test_property_student_budget_routing(country: str, budget: float) -> None:
    """Student budgets follow the same ETB/USD routing rule (Requirements 6.2, 6.3)."""
    etb_budget, usd_budget = budget_fields(country, budget)
    if is_ethiopia(country):
        assert etb_budget == pytest.approx(budget)
        assert usd_budget is None
    else:
        assert usd_budget == pytest.approx(budget)
        assert etb_budget is None


@given(
    country=COUNTRIES,
    budget=st.floats(min_value=5, max_value=9000),
    seed=st.integers(min_value=1, max_value=10**6),
)
@PROFILE
def test_property_student_request_stores_routed_budget(
    fresh_db, country: str, budget: float, seed: int
) -> None:
    session = fresh_db()
    request = create_student_request(
        session,
        student_request_payload(-(seed + 1), country=country, budget=budget),
    )
    session.commit()
    if is_ethiopia(country):
        assert request.currency == "ETB"
        assert request.etb_budget == pytest.approx(budget)
        assert request.usd_budget is None
    else:
        assert request.currency == "USD"
        assert request.usd_budget == pytest.approx(budget)
        assert request.etb_budget is None


def test_request_ids_are_unique_and_formatted(fresh_db) -> None:
    session = fresh_db()
    ids = [
        create_student_request(
            session, student_request_payload(-(index + 1))
        ).public_request_id
        for index in range(5)
    ]
    session.commit()
    assert len(set(ids)) == 5
    assert all(value.startswith("REQ-") for value in ids)