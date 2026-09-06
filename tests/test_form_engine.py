"""The form engine: branching, volunteered answers, corrections, refusals, and resuming a drop.

These are the behaviours a phone intake lives or dies on, and every one of them is a bug that only shows
up on a real call unless it is pinned here.
"""

from __future__ import annotations

from form_engine import (
    AnswerStatus,
    ExtractedAnswer,
    IntakeEngine,
    IntakeState,
    engine_from_records,
    expanded_id,
)
from form_intake import INTAKE


def _engine() -> IntakeEngine:
    return IntakeEngine(INTAKE)


def _consented(form: IntakeEngine) -> IntakeEngine:
    form.record("reached_right_party", "homeowner")
    form.record("homeowner_confirmed", "yes")
    form.record("recording_disclosure_ack", "yes")
    form.record("consent_to_continue", "yes")
    return form


# ------------------------------------------------------------ order


def test_the_first_question_is_who_answered():
    assert _engine().next_field().id == "reached_right_party"


def test_questions_come_in_the_order_the_form_declares():
    form = _engine()
    asked = []
    for _ in range(4):
        field = form.next_field()
        asked.append(field.id)
        form.record(field.id, "yes" if field.type.value == "bool" else "homeowner")
    assert asked == [
        "reached_right_party",
        "homeowner_confirmed",
        "recording_disclosure_ack",
        "consent_to_continue",
    ]


# ------------------------------------------------------------ branching


def test_a_branch_stays_shut_until_its_gate_opens():
    form = _consented(_engine())
    assert "mailing_address" not in [one.field.id for one in form.plan() if form.applies(one.field)]
    form.record("mailing_same_as_property", "no")
    assert form.applies(INTAKE.field("mailing_address")) is True


def test_a_closed_branch_is_never_asked():
    form = _consented(_engine())
    form.record("mailing_same_as_property", "yes")
    assert form.applies(INTAKE.field("mailing_address")) is False


def test_occupancy_opens_the_questions_that_belong_to_it():
    form = _consented(_engine())
    form.record("occupancy", "vacant")
    assert form.applies(INTAKE.field("vacant_since_months")) is True
    assert form.applies(INTAKE.field("rental_months_per_year")) is False


def test_a_rental_opens_the_letting_questions_instead():
    form = _consented(_engine())
    form.record("occupancy", "rental")
    assert form.applies(INTAKE.field("rental_months_per_year")) is True
    assert form.applies(INTAKE.field("vacant_since_months")) is False


def test_an_old_house_is_asked_about_its_wiring():
    form = _consented(_engine())
    form.record("year_built", "1952")
    assert form.applies(INTAKE.field("electrical_knob_and_tube")) is True


def test_a_new_house_is_not():
    form = _consented(_engine())
    form.record("year_built", "2011")
    assert form.applies(INTAKE.field("electrical_knob_and_tube")) is False


def test_a_wood_stove_opens_its_follow_ups():
    form = _consented(_engine())
    form.record("secondary_heat", "wood stove")
    assert form.applies(INTAKE.field("wood_stove_inspected")) is True


def test_a_pool_opens_the_fence_question():
    form = _consented(_engine())
    form.record("pool", "yes")
    assert form.applies(INTAKE.field("pool_fenced")) is True


def test_the_whole_auto_section_is_conditional():
    form = _consented(_engine())
    form.record("bundle_interest", "no")
    assert form.applies(INTAKE.field("vehicle_count")) is False


def test_a_branch_whose_gate_was_refused_is_undecidable_rather_than_closed():
    """Refusing the gate must not silently close what depended on it."""
    form = _consented(_engine())
    form.refuse("pool", "none of your business")
    assert form.applies(INTAKE.field("pool_fenced")) is None
    assert "pool_fenced" in form.blocked()


# ------------------------------------------------------------ repeating groups


def test_claims_expand_into_one_block_per_claim():
    form = _consented(_engine())
    form.record("claims_last_five_years", "yes")
    form.record("claims_count", "two")
    ids = [one.field.id for one in form.plan()]
    assert expanded_id("claim_year", 1) in ids
    assert expanded_id("claim_year", 2) in ids
    assert expanded_id("claim_year", 3) not in ids


def test_a_repeat_block_says_which_item_it_is_asking_about():
    form = _consented(_engine())
    form.record("claims_last_five_years", "yes")
    form.record("claims_count", "two")
    item = form.item_for(expanded_id("claim_year", 2))
    assert item is not None
    assert item.prompt().startswith("For the second claim:")


def test_a_within_item_branch_is_resolved_per_item():
    """The repair question belongs only to a claim that actually paid out."""
    form = _consented(_engine())
    form.record("claims_last_five_years", "yes")
    form.record("claims_count", "two")
    form.record(expanded_id("claim_amount_paid", 1), "4200")
    form.record(expanded_id("claim_amount_paid", 2), "0")
    assert form.applies(form.item_for(expanded_id("claim_repaired", 1)).field) is True
    assert form.applies(form.item_for(expanded_id("claim_repaired", 2)).field) is False


def test_a_repeat_group_is_capped():
    form = _consented(_engine())
    form.record("claims_last_five_years", "yes")
    form.record("claims_count", "twelve")
    years = [one for one in form.plan() if one.field.id.startswith("claim_year#")]
    assert len(years) == INTAKE.group("claims").maximum


# ------------------------------------------------------------ volunteered and out of order


def test_a_batch_of_volunteered_answers_all_land():
    form = _consented(_engine())
    results = form.record_batch(
        [
            ExtractedAnswer("year_built", "1998"),
            ExtractedAnswer("roof_age_years", "four"),
            ExtractedAnswer("claims_last_five_years", "no"),
        ]
    )
    assert [one.accepted for one in results] == [True, True, True]
    assert form.values()["year_built"] == 1998
    assert form.values()["roof_age_years"] == 4
    assert form.values()["claims_last_five_years"] is False


def test_a_volunteered_answer_is_never_asked_again():
    form = _consented(_engine())
    form.record_batch([ExtractedAnswer("year_built", "1998")])
    remaining = [one.id for one in form.outstanding()]
    assert "year_built" not in remaining


def test_an_answer_given_before_its_branch_opens_still_lands():
    """A caller who says "two claims, both hail" has answered inside a group that does not exist yet."""
    form = _consented(_engine())
    form.record("claims_last_five_years", "yes")
    form.record("claims_count", "two")
    result = form.record(expanded_id("claim_type", 1), "wind hail")
    assert result.accepted
    assert form.values()[expanded_id("claim_type", 1)] == "wind_hail"


def test_a_batch_applies_in_the_order_it_was_said():
    """The count has to land before the claim details it opens."""
    form = _consented(_engine())
    results = form.record_batch(
        [
            ExtractedAnswer("claims_last_five_years", "yes"),
            ExtractedAnswer("claims_count", "one"),
            ExtractedAnswer(expanded_id("claim_year", 1), "2023"),
        ]
    )
    assert all(one.accepted for one in results)


# ------------------------------------------------------------ corrections


def test_a_correction_keeps_what_was_first_said():
    form = _consented(_engine())
    form.record("roof_age_years", "five")
    result = form.record("roof_age_years", "actually seven")
    assert result.corrected
    answer = form.answers["roof_age_years"]
    assert answer.value == 7
    assert len(answer.revisions) == 1
    assert answer.revisions[0].value == 5


def test_repeating_the_same_answer_is_not_a_correction():
    form = _consented(_engine())
    form.record("roof_age_years", "five")
    result = form.record("roof_age_years", "five")
    assert result.corrected is False
    assert form.answers["roof_age_years"].revisions == []


def test_a_correction_reopens_the_branch_it_changes():
    form = _consented(_engine())
    form.record("occupancy", "rental")
    assert form.applies(INTAKE.field("rental_months_per_year")) is True
    form.record("occupancy", "actually it is our main home")
    assert form.applies(INTAKE.field("rental_months_per_year")) is False


# ------------------------------------------------------------ refusals and not knowing


def test_a_refusal_is_stored_with_the_words_used():
    form = _consented(_engine())
    form.refuse("current_premium_annual", "I would rather not say")
    answer = form.answers["current_premium_annual"]
    assert answer.status is AnswerStatus.REFUSED
    assert answer.value is None
    assert answer.verbatim == "I would rather not say"
    assert "current_premium_annual" in form.refused()


def test_not_knowing_is_different_from_refusing():
    form = _consented(_engine())
    form.mark_unknown("plumbing_material", "no idea")
    assert "plumbing_material" in form.unknown()
    assert "plumbing_material" not in form.refused()


def test_refusing_consent_is_fatal_and_refusing_a_premium_is_not():
    form = _engine()
    assert form.refuse("current_premium_annual", "no").fatal is False
    assert form.refuse("consent_to_continue", "no thanks").fatal is True
    assert form.state is IntakeState.ENDED_FATAL_REFUSAL


def test_a_refused_field_is_not_outstanding():
    form = _consented(_engine())
    form.refuse("current_premium_annual", "no")
    assert "current_premium_annual" not in [one.id for one in form.outstanding()]


# ------------------------------------------------------------ uncertainty


def test_a_hedged_number_is_kept_at_low_confidence():
    form = _consented(_engine())
    form.record("roof_age_years", "I think about ten years?")
    answer = form.answers["roof_age_years"]
    assert answer.value == 10
    assert answer.confidence.value == "low"
    assert "roof_age_years" in form.low_confidence()


def test_a_direct_number_is_kept_at_high_confidence():
    form = _consented(_engine())
    form.record("roof_age_years", "ten")
    assert form.answers["roof_age_years"].confidence.value == "high"


# ------------------------------------------------------------ validation


def test_an_unreadable_answer_is_not_stored():
    form = _consented(_engine())
    result = form.record("year_built", "sometime after the war")
    assert result.accepted is False
    assert "year" in result.problem
    assert "year_built" not in form.answers


def test_a_value_outside_its_range_is_refused_with_a_reason():
    form = _consented(_engine())
    result = form.record("square_feet", "12")
    assert result.accepted is False
    assert "below" in result.problem


def test_an_unknown_field_is_reported_rather_than_swallowed():
    form = _engine()
    result = form.record("no_such_field", "yes")
    assert result.accepted is False
    assert "no such field" in result.problem


# ------------------------------------------------------------ resuming


def test_an_engine_rebuilt_from_rows_resumes_where_it_stopped():
    records = [
        {"field_id": "reached_right_party", "value": "homeowner", "status": "answered", "verbatim": "yes", "confidence": "high", "sequence": 1},
        {"field_id": "homeowner_confirmed", "value": True, "status": "answered", "verbatim": "yes", "confidence": "high", "sequence": 2},
        {"field_id": "recording_disclosure_ack", "value": True, "status": "answered", "verbatim": "fine", "confidence": "high", "sequence": 3},
        {"field_id": "consent_to_continue", "value": True, "status": "answered", "verbatim": "go on", "confidence": "high", "sequence": 4},
    ]
    form = engine_from_records(INTAKE, records)
    assert form.next_field().id == "contact_name"
    assert form.state is IntakeState.IN_PROGRESS


def test_resuming_does_not_lose_a_refusal():
    records = [
        {"field_id": "current_premium_annual", "value": None, "status": "refused", "verbatim": "no", "confidence": "high", "sequence": 9},
    ]
    form = engine_from_records(INTAKE, records)
    assert form.refused() == ["current_premium_annual"]


def test_resuming_continues_the_sequence_rather_than_restarting_it():
    records = [
        {"field_id": "contact_name", "value": "Marguerite Halloway", "status": "answered", "verbatim": "Marguerite Halloway", "confidence": "high", "sequence": 5},
    ]
    form = engine_from_records(INTAKE, records)
    form.record("preferred_language", "english")
    assert form.answers["preferred_language"].sequence == 6


# ------------------------------------------------------------ progress and completion


def test_progress_counts_only_what_this_call_will_ask():
    form = _consented(_engine())
    form.record("bundle_interest", "no")
    answered, total = form.progress()
    assert answered == 5
    assert total < len(INTAKE.fields)


def test_missing_required_lists_what_a_quote_still_needs():
    form = _consented(_engine())
    missing = form.missing_required()
    assert "property_address" in missing
    assert "reached_right_party" not in missing


def test_a_summary_carries_everything_a_licensed_agent_needs():
    form = _consented(_engine())
    form.record("roof_age_years", "about twelve")
    form.refuse("current_premium_annual", "rather not")
    summary = form.summary()
    assert summary["state"] == "in_progress"
    assert summary["refused"] == ["current_premium_annual"]
    assert summary["low_confidence"] == ["roof_age_years"]
    assert summary["values"]["roof_age_years"] == 12
