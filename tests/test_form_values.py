"""Parsing what people actually say. Every case here came from how a number arrives over a phone line."""

from __future__ import annotations

import pytest
from form_intake import INTAKE
from form_spec import AnswerType, Field
from form_values import confidence_for, hedged, normalize, spoken_digit_string


def _field(field_id: str) -> Field:
    field = INTAKE.field(field_id)
    assert field is not None
    return field


@pytest.mark.parametrize(
    "said,expected",
    [
        ("nineteen ninety eight", 1998),
        ("1998", 1998),
        ("built in 2004", 2004),
        ("twenty twenty one", 2021),
        ("eighteen ninety", 1890),
        ("one nine nine eight", 1998),
    ],
)
def test_a_year_is_read_however_it_is_said(said, expected):
    assert normalize(_field("year_built"), said)[0] == expected


def test_a_year_in_the_future_is_refused():
    value, problem = normalize(_field("renovation_year"), "2099")
    assert value is None
    assert "future" in problem


@pytest.mark.parametrize(
    "said,expected",
    [
        ("about two thousand four hundred", 2400),
        ("twenty six hundred", 2600),
        ("1,850", 1850),
        ("thirty two hundred", 3200),
        ("2000", 2000),
    ],
)
def test_square_footage_is_read_however_it_is_said(said, expected):
    assert normalize(_field("square_feet"), said)[0] == expected


@pytest.mark.parametrize(
    "said,expected",
    [
        ("twelve hundred", 1200),
        ("$1,850 a year", 1850),
        ("about 2k", 2000),
    ],
)
def test_money_is_read_however_it_is_said(said, expected):
    assert normalize(_field("current_premium_annual"), said)[0] == expected


def test_a_figure_in_the_millions_is_read_where_the_field_allows_it():
    assert normalize(_field("current_dwelling_coverage"), "1.2 million")[0] == 1200000


def test_an_implausible_premium_is_refused_rather_than_stored():
    """A range is the only thing standing between a misheard figure and a quote built on it."""
    value, problem = normalize(_field("current_premium_annual"), "1.2 million")
    assert value is None
    assert "above" in problem


@pytest.mark.parametrize(
    "said,expected",
    [
        ("yes", True),
        ("yeah, two of them", True),
        ("that's right", True),
        ("no", False),
        ("nope, never", False),
        ("not really", False),
        ("we don't have one", False),
    ],
)
def test_yes_and_no_survive_a_tail(said, expected):
    assert normalize(_field("pool"), said)[0] is expected


def test_an_answer_that_is_neither_yes_nor_no_is_refused():
    value, problem = normalize(_field("pool"), "well it depends what you mean")
    assert value is None
    assert "yes or no" in problem


@pytest.mark.parametrize(
    "said,expected",
    [
        ("six one four five five five zero one one eight", "6146145550118"[3:]),
        ("614 555 0118", "6145550118"),
        ("+1 614 555 0118", "6145550118"),
        ("six one four, five five five, oh one one eight", "6145550118"),
        ("six one four, triple five, oh one one eight", "6145550118"),
    ],
)
def test_a_phone_number_is_read_from_digits_or_words(said, expected):
    assert normalize(_field("callback_number_confirm"), said)[0] == expected


def test_double_and_triple_repeat_the_digit_that_follows():
    assert spoken_digit_string("double five") == "55"
    assert spoken_digit_string("triple seven") == "777"
    assert spoken_digit_string("one double two three") == "1223"


def test_a_spoken_email_is_recovered():
    value, problem = normalize(_field("quote_email"), "m dot halloway at example dot com")
    assert problem is None
    assert value == "m.halloway@example.com"


def test_something_that_is_not_an_email_is_refused():
    value, problem = normalize(_field("quote_email"), "I would rather not")
    assert value is None
    assert "email" in problem


def test_an_address_needs_a_number_in_it():
    value, problem = normalize(_field("property_address"), "just off the main road")
    assert value is None
    assert "street address" in problem


def test_an_address_is_kept_exactly_as_said():
    said = "2841 Wexford Lane, Dublin, Ohio 43017"
    assert normalize(_field("property_address"), said)[0] == said


@pytest.mark.parametrize("said", ["I think about ten", "roughly ten years", "ten or so", "maybe ten"])
def test_a_hedged_number_is_marked_hedged(said):
    assert hedged(said) is True
    assert confidence_for(said, 10).value == "low"


def test_a_direct_number_is_not_hedged():
    assert hedged("ten years") is False
    assert confidence_for("ten years", 10).value == "high"


def test_a_long_ramble_carrying_a_number_is_only_medium_confidence():
    said = (
        "well we had the whole thing done when we moved in which was a while back now and the man said "
        "it would last twenty five years so I suppose it is about ten"
    )
    assert confidence_for(said, 10).value in {"low", "medium"}


def test_a_date_is_read_from_several_shapes():
    assert normalize(_field("policy_expiry_date"), "2027-03-14")[0].isoformat() == "2027-03-14"
    assert normalize(_field("policy_expiry_date"), "3/14/2027")[0].isoformat() == "2027-03-14"
    assert normalize(_field("policy_expiry_date"), "14th March 2027")[0].isoformat() == "2027-03-14"


def test_nothing_said_is_reported_rather_than_stored():
    value, problem = normalize(_field("year_built"), "   ")
    assert value is None
    assert "nothing was said" in problem


def test_an_enum_maps_from_its_own_words():
    assert normalize(_field("construction_type"), "timber frame")[0] == "frame"
    assert normalize(_field("garage_type"), "separate, out the back")[0] == "detached"


def test_a_deductible_maps_from_a_spoken_amount():
    assert normalize(_field("desired_deductible"), "a thousand")[0] == "1000"
    assert normalize(_field("desired_deductible"), "500")[0] == "500"


def test_every_enum_field_declares_choices():
    """A choice list is what the agent maps onto, so an enum without one cannot be answered."""
    missing = [
        one.id for one in INTAKE.fields if one.type is AnswerType.ENUM and not one.choices
    ]
    assert missing == []
