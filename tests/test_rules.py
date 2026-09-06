"""Eligibility. Every rule fires on the shape it is for, and nothing fires on a missing answer."""

from __future__ import annotations

import pytest
from form_intake import INTAKE, LICENSED_STATES
from rules import HARD_CODES, RULES, SOFT_CODES, Severity, rule
from rules_engine import evaluate, fields_in

QUOTABLE = {
    "homeowner_confirmed": True,
    "property_state": "OH",
    "occupancy": "primary",
    "year_built": 2004,
    "square_feet": 2400,
    "roof_age_years": 6,
    "roof_material": "architectural_shingle",
    "claims_last_five_years": False,
    "prior_cancellation": False,
    "currently_insured": True,
    "smoke_detectors": True,
    "pool": False,
    "trampoline": False,
    "dogs": False,
    "business_at_home": False,
    "secondary_heat": "none",
    "electrical_update_year": 2015,
    "plumbing_material": "copper",
    "fire_station_miles": 2,
    "hydrant_distance_feet": 300,
}


def _with(**overrides) -> dict:
    return {**QUOTABLE, **overrides}


def test_a_clean_intake_is_eligible_with_no_flags():
    result = evaluate(_with())
    assert result.decision.value == "eligible"
    assert result.hard == ()
    assert result.soft == ()
    assert result.transferable is True


# ------------------------------------------------------------ hard disqualifiers


@pytest.mark.parametrize(
    "code,answers",
    [
        ("renter_not_homeowner", {"homeowner_confirmed": False}),
        ("outside_licensed_states", {"property_state": "California"}),
        ("vacant_property", {"occupancy": "vacant"}),
        ("roof_too_old_asphalt", {"roof_material": "asphalt_shingle", "roof_age_years": 24}),
        ("roof_too_old_other", {"roof_material": "metal", "roof_age_years": 45}),
        ("too_many_claims", {"claims_last_five_years": True, "claims_count": 3}),
        ("cancelled_for_non_payment", {"prior_cancellation": True, "cancellation_reason": "non_payment"}),
        ("restricted_dog_breed", {"dogs": True, "dog_breed": "a rottweiler cross"}),
        ("wood_stove_uninspected", {"secondary_heat": "wood_stove", "wood_stove_inspected": False}),
        ("coverage_lapse", {"currently_insured": False, "coverage_lapse_days": 120}),
        ("short_term_rental", {"occupancy": "rental", "rental_short_term_platform": True}),
        ("dog_bite_history", {"dogs": True, "dog_breed": "labrador", "dog_bite_history": True}),
        ("knob_and_tube_wiring", {"year_built": 1949, "electrical_knob_and_tube": True}),
        (
            "commercial_exposure",
            {"business_at_home": True, "business_employees_onsite": 4, "business_client_visits": True},
        ),
        ("vacant_long_term", {"occupancy": "vacant", "vacant_since_months": 9}),
    ],
)
def test_each_hard_rule_fires_and_disqualifies(code, answers):
    result = evaluate(_with(**answers))
    assert code in [one.code for one in result.hard], f"{code} did not fire"
    assert result.decision.value == "disqualified"
    assert result.transferable is False


def test_a_disqualified_intake_has_something_plain_to_say():
    result = evaluate(_with(occupancy="vacant"))
    spoken = result.spoken_reason()
    assert spoken
    assert "vacancy" in spoken or "empty" in spoken


def test_every_hard_rule_carries_a_reason_that_can_be_spoken():
    """A decline the caller cannot be given a reason for is not a decline anybody can defend."""
    silent = [one.code for one in RULES if one.severity is Severity.HARD and not one.reason.strip()]
    assert silent == []


def test_the_first_hard_reason_is_the_one_spoken_when_two_apply():
    result = evaluate(
        _with(roof_material="asphalt_shingle", roof_age_years=25, claims_last_five_years=True, claims_count=3)
    )
    assert len(result.hard) == 2
    assert result.spoken_reason() == result.hard[0].reason


# ------------------------------------------------------------ soft flags


@pytest.mark.parametrize(
    "code,answers",
    [
        ("roof_ageing", {"roof_age_years": 18}),
        ("claims_moderate", {"claims_last_five_years": True, "claims_count": 2}),
        ("repeat_water_claims", {"claim_type#1": "water", "claim_type#2": "water"}),
        ("unrepaired_damage", {"claim_repaired#1": False}),
        ("prior_cancellation_other", {"prior_cancellation": True, "cancellation_reason": "underwriting"}),
        ("unfenced_pool", {"pool": True, "pool_fenced": False}),
        ("pool_diving_board_present", {"pool": True, "pool_diving_board": True}),
        ("trampoline_present", {"trampoline": True}),
        ("wood_shake_roof", {"roof_material": "wood_shake"}),
        ("polybutylene_plumbing", {"plumbing_material": "polybutylene"}),
        ("buried_oil_tank", {"heating_type": "oil", "oil_tank_location": "underground"}),
        ("far_from_protection", {"fire_station_miles": 9}),
        ("old_home_no_updates", {"year_built": 1955, "electrical_update_year": 1960, "electrical_knob_and_tube": False}),
        ("dwelling_coverage_low", {"current_dwelling_coverage": 120000, "square_feet": 2400}),
        ("suspended_licence", {"driver_licence_status#1": "suspended"}),
        ("driver_incidents_high", {"driver_incidents_three_years#1": 4}),
        ("no_smoke_detectors", {"smoke_detectors": False}),
    ],
)
def test_each_soft_rule_flags_without_blocking(code, answers):
    result = evaluate(_with(**answers))
    assert code in [one.code for one in result.soft], f"{code} did not flag"
    assert result.decision.value == "eligible"
    assert result.transferable is True


def test_every_soft_rule_carries_a_note_for_the_licensed_agent():
    """A flag nobody can act on is noise on a handover."""
    silent = [one.code for one in RULES if one.severity is Severity.SOFT and not one.internal_note.strip()]
    assert silent == []


def test_a_soft_flag_never_speaks_to_the_caller():
    for one in RULES:
        if one.severity is Severity.SOFT:
            assert one.reason == "", f"{one.code} would be spoken to the caller"


# ------------------------------------------------------------ missing and refused answers


def test_a_refused_answer_makes_a_hard_rule_undecidable_rather_than_passing_it():
    answers = _with()
    answers.pop("occupancy")
    result = evaluate(answers)
    assert result.decision.value == "needs_review"
    assert "vacant_property" in [one.code for one in result.indeterminate]


def test_an_undecidable_rule_names_what_is_missing():
    answers = _with()
    answers.pop("roof_age_years")
    result = evaluate(answers)
    undecided = {one.code: one.missing for one in result.indeterminate}
    assert "roof_age_years" in undecided["roof_too_old_asphalt"]


def test_a_needs_review_intake_can_still_be_transferred():
    answers = _with()
    answers.pop("prior_cancellation")
    result = evaluate(answers)
    assert result.decision.value == "needs_review"
    assert result.transferable is True


def test_an_empty_intake_does_not_crash_and_does_not_pass():
    result = evaluate({})
    assert result.decision.value == "needs_review"
    assert result.hard == ()


def test_a_soft_rule_that_cannot_be_decided_is_not_reported():
    """Only hard rules block, so an unsettleable flag would be noise on the review list."""
    result = evaluate({})
    assert all(rule(one.code).severity is Severity.HARD for one in result.indeterminate)


# ------------------------------------------------------------ derived values


def test_ages_are_measured_from_the_campaign_reference_year():
    early = evaluate(_with(year_built=1955, electrical_update_year=1960), reference_year=2000)
    late = evaluate(_with(year_built=1955, electrical_update_year=1960), reference_year=2026)
    assert "old_home_no_updates" not in [one.code for one in early.soft]
    assert "old_home_no_updates" in [one.code for one in late.soft]


def test_a_state_name_and_a_state_code_reach_the_same_verdict():
    assert evaluate(_with(property_state="Ohio")).decision.value == "eligible"
    assert evaluate(_with(property_state="OH")).decision.value == "eligible"


def test_a_state_outside_the_footprint_is_caught_by_either_spelling():
    assert evaluate(_with(property_state="Nevada")).decision.value == "disqualified"
    assert evaluate(_with(property_state="NV")).decision.value == "disqualified"


def test_never_updated_wiring_counts_as_being_as_old_as_the_house():
    answers = _with(year_built=1950)
    answers.pop("electrical_update_year")
    assert "old_home_no_updates" in [one.code for one in evaluate(answers).soft]


def test_a_restricted_breed_is_matched_inside_a_longer_answer():
    listed = _with(dogs=True, dog_breed="he's a pit bull mix", dog_bite_history=False)
    ordinary = _with(dogs=True, dog_breed="a beagle", dog_bite_history=False)
    assert evaluate(listed).decision.value == "disqualified"
    assert evaluate(ordinary).decision.value == "eligible"


def test_opening_a_branch_and_leaving_it_unanswered_reaches_review_rather_than_eligible():
    """The honest outcome. A dog nobody asked about cannot be cleared by assuming it never bit anybody."""
    result = evaluate(_with(dogs=True, dog_breed="a beagle"))
    assert result.decision.value == "needs_review"
    assert "dog_bite_history" in [one.code for one in result.indeterminate]


# ------------------------------------------------------------ the catalogue itself


def test_rule_codes_are_unique():
    codes = [one.code for one in RULES]
    assert len(codes) == len(set(codes))


def test_hard_and_soft_partition_the_catalogue():
    assert len(HARD_CODES) + len(SOFT_CODES) == len(RULES)


def test_every_field_a_rule_reads_exists_on_the_form_or_is_derived():
    """A rule reading a field nobody asks can never fire, and nothing would say so."""
    from rules_derived import derive

    derived_names = set(derive({"year_built": 2000, "dog_breed": "x", "property_state": "OH"}))
    derived_names |= {
        "water_claim_count", "unrepaired_claim_count", "claim_amount_total", "claim_amount_max",
        "max_driver_incidents", "suspended_licence_count", "dwelling_coverage_per_sqft",
        "claim_details_given", "years_since_plumbing_update",
    }
    form_names = {one.id for one in INTAKE.fields}
    unknown = []
    for one in RULES:
        for name in fields_in(one.predicate):
            base = name.split("#")[0]
            if base not in form_names and base not in derived_names:
                unknown.append((one.code, name))
    assert unknown == []


def test_every_rule_named_by_a_field_exists_in_the_catalogue():
    """The other direction: a field claiming to feed a rule that was deleted is a stale comment."""
    codes = {one.code for one in RULES}
    dangling = [
        (one.id, name) for one in INTAKE.fields for name in one.feeds_rules if name not in codes
    ]
    assert dangling == []


def test_the_licensed_footprint_is_not_empty():
    assert len(LICENSED_STATES) >= 10
