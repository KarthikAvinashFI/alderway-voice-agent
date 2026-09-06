"""Push-back handling, and the line the agent will not cross whatever it is asked."""

from __future__ import annotations

import pytest
from alderway_voice_agent.objections import (
    BOUNDARIES,
    PLAYBOOK,
    BoundaryKind,
    ObjectionExit,
    ObjectionTracker,
    crossed,
    deflection_for,
)


def _tracker() -> ObjectionTracker:
    return ObjectionTracker()


# ------------------------------------------------------------ recognition


@pytest.mark.parametrize(
    "said,code",
    [
        ("take me off your list", "remove_me"),
        ("do not call me again", "remove_me"),
        ("stop calling me", "remove_me"),
        ("sorry, I'm driving", "im_driving"),
        ("I'm in the car right now", "im_driving"),
        ("can I speak to a real person", "wants_human"),
        ("are you a bot", "wants_human"),
        ("not interested, thanks", "not_interested"),
        ("how did you get my number", "how_did_you_get_my_number"),
        ("I already have insurance", "already_insured"),
        ("is this a scam", "is_this_a_scam"),
        ("call me later, I'm busy right now", "call_me_later"),
        ("this is taking too long", "too_many_questions"),
        ("leave me alone", "hostile"),
    ],
)
def test_an_objection_is_recognised_from_how_it_is_said(said, code):
    assert _tracker().classify(said) == code


def test_an_ordinary_answer_is_not_an_objection():
    assert _tracker().classify("it was built in 1998") is None
    assert _tracker().classify("the roof is about six years old") is None


def test_a_removal_request_is_recognised_before_anything_softer():
    """Both cues are present. The one that ends the call has to win."""
    assert _tracker().classify("I'm not interested, take me off your list") == "remove_me"


# ------------------------------------------------------------ immediate exits


def test_a_removal_request_is_honoured_on_the_first_mention():
    outcome = _tracker().handle("remove_me")
    assert outcome.exit is ObjectionExit.HONOUR_REMOVAL


def test_driving_ends_the_call_on_the_first_mention():
    outcome = _tracker().handle("im_driving")
    assert outcome.exit is ObjectionExit.SCHEDULE_CALLBACK
    assert "driving" in outcome.say


def test_asking_for_a_person_transfers_on_the_first_mention():
    outcome = _tracker().handle("wants_human")
    assert outcome.exit is ObjectionExit.TRANSFER_HUMAN


def test_hostility_ends_the_call_rather_than_being_argued_with():
    outcome = _tracker().handle("hostile")
    assert outcome.exit is ObjectionExit.HONOUR_REMOVAL


def test_an_immediate_objection_is_never_rebutted_twice():
    tracker = _tracker()
    first = tracker.handle("im_driving")
    second = tracker.handle("im_driving")
    assert first.exit is second.exit is ObjectionExit.SCHEDULE_CALLBACK


# ------------------------------------------------------------ bounded attempts


def test_not_interested_gets_one_rebuttal_then_stops():
    tracker = _tracker()
    first = tracker.handle("not_interested")
    assert first.exit is ObjectionExit.CONTINUE
    assert first.say
    second = tracker.handle("not_interested")
    assert second.exit is ObjectionExit.END_POLITE
    third = tracker.handle("not_interested")
    assert third.exit is ObjectionExit.END_POLITE
    assert third.say == ""


def test_asking_where_the_number_came_from_twice_gets_them_removed():
    tracker = _tracker()
    assert tracker.handle("how_did_you_get_my_number").exit is ObjectionExit.CONTINUE
    assert tracker.handle("how_did_you_get_my_number").exit is ObjectionExit.HONOUR_REMOVAL


def test_already_insured_ends_with_a_callback_rather_than_a_third_try():
    tracker = _tracker()
    assert tracker.handle("already_insured").exit is ObjectionExit.CONTINUE
    assert tracker.handle("already_insured").exit is ObjectionExit.SCHEDULE_CALLBACK


def test_too_many_questions_ends_by_handing_over_to_a_person():
    tracker = _tracker()
    assert tracker.handle("too_many_questions").exit is ObjectionExit.CONTINUE
    assert tracker.handle("too_many_questions").exit is ObjectionExit.TRANSFER_HUMAN


def test_every_playbook_entry_has_somewhere_to_end():
    for one in PLAYBOOK:
        assert one.on_exhausted is not ObjectionExit.CONTINUE, one.code


def test_no_entry_argues_more_than_twice():
    for one in PLAYBOOK:
        assert one.max_attempts <= 2, one.code


def test_the_scam_answer_names_what_will_never_be_asked_for():
    said = _tracker().handle("is_this_a_scam").say
    assert "bank" in said and "card" in said


# ------------------------------------------------------------ the total budget


def test_enough_objections_of_any_kind_end_the_call():
    tracker = _tracker()
    assert tracker.over_budget() is False
    for code in ("not_interested", "already_insured", "call_me_later", "too_many_questions", "is_this_a_scam"):
        tracker.handle(code)
    assert tracker.over_budget() is True


def test_an_unknown_code_changes_nothing():
    tracker = _tracker()
    outcome = tracker.handle("no_such_objection")
    assert outcome.exit is ObjectionExit.CONTINUE
    assert tracker.raised == 0


# ------------------------------------------------------------ the licensed agent boundary


@pytest.mark.parametrize(
    "said,kind",
    [
        ("so how much will it be", BoundaryKind.QUOTE_PRICE),
        ("roughly how much am I looking at", BoundaryKind.QUOTE_PRICE),
        ("just give me a quote", BoundaryKind.QUOTE_PRICE),
        ("what deductible should i get", BoundaryKind.RECOMMEND_COVERAGE),
        ("what do you recommend", BoundaryKind.RECOMMEND_COVERAGE),
        ("is that enough cover", BoundaryKind.RECOMMEND_COVERAGE),
        ("am i covered for flooding", BoundaryKind.CONFIRM_COVERED),
        ("does it cover the shed", BoundaryKind.CONFIRM_COVERED),
        ("sign me up then", BoundaryKind.BIND_OR_CANCEL),
        ("cancel my current policy", BoundaryKind.BIND_OR_CANCEL),
        ("should i claim for it", BoundaryKind.CLAIM_ADVICE),
        ("is that legal", BoundaryKind.LEGAL_OR_TAX),
    ],
)
def test_a_request_for_advice_or_a_price_is_recognised(said, kind):
    rule = crossed(said)
    assert rule is not None, said
    assert rule.kind is kind


def test_an_ordinary_answer_crosses_nothing():
    assert crossed("the roof was done four years ago") is None
    assert crossed("we have two cars") is None


def test_every_deflection_refuses_and_offers_a_person():
    for one in BOUNDARIES:
        assert one.say
        assert "licensed agent" in one.say


def test_no_deflection_contains_a_number_that_could_read_as_a_price():
    for one in BOUNDARIES:
        assert not any(character.isdigit() for character in one.say), one.kind


def test_the_price_deflection_says_plainly_that_it_cannot():
    said = deflection_for("how much will it be")
    assert "not able to give you a price" in said


def test_nothing_is_deflected_when_no_boundary_is_crossed():
    assert deflection_for("built in 1998") == ""
