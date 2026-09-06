"""Values a rule needs that no single question produces.

Two kinds live here. Ages, which depend on today rather than on what was said. And anything that reads
across a repeating group: three claims are three sets of answers, and "how many of them were water"
is not a question anybody asks out loud.

Kept separate from the rules so a rule stays a one line predicate, and separate from the form so the
form never has to ask something it can work out.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from form_intake import RESTRICTED_DOG_BREEDS

# Fixed rather than read from the clock, so a rule's outcome is reproducible when a call is replayed
# months later. The campaign sets it.
DEFAULT_REFERENCE_YEAR = 2026


def _group_values(values: Mapping[str, Any], template_id: str) -> list[Any]:
    """Every answer given for one field of a repeating group, in item order."""
    found: list[tuple[int, Any]] = []
    pattern = re.compile(rf"^{re.escape(template_id)}#(\d+)$")
    for key, value in values.items():
        match = pattern.match(key)
        if match and value is not None:
            found.append((int(match.group(1)), value))
    return [value for _, value in sorted(found)]


def derive(values: Mapping[str, Any], *, reference_year: int = DEFAULT_REFERENCE_YEAR) -> dict[str, Any]:
    """Computed values, keyed the way a rule refers to them.

    Absent inputs produce an absent output rather than a zero. A missing derived value leaves a rule
    undecidable, which is the honest outcome and the one the engine reports for review.
    """
    out: dict[str, Any] = {}

    year_built = values.get("year_built")
    if isinstance(year_built, int):
        out["home_age"] = reference_year - year_built

    electrical = values.get("electrical_update_year")
    if isinstance(electrical, int):
        out["years_since_electrical_update"] = reference_year - electrical
    elif isinstance(year_built, int):
        # Never updated means it is as old as the house, which is the case the rule is aimed at.
        out["years_since_electrical_update"] = reference_year - year_built

    plumbing = values.get("plumbing_update_year")
    if isinstance(plumbing, int):
        out["years_since_plumbing_update"] = reference_year - plumbing

    breed = values.get("dog_breed")
    if isinstance(breed, str) and breed.strip():
        lowered = breed.lower()
        out["dog_breed_restricted"] = any(one in lowered for one in RESTRICTED_DOG_BREEDS)

    claim_types = _group_values(values, "claim_type")
    if claim_types:
        out["water_claim_count"] = sum(1 for one in claim_types if one == "water")
        out["claim_details_given"] = len(claim_types)

    repaired = _group_values(values, "claim_repaired")
    if repaired:
        out["unrepaired_claim_count"] = sum(1 for one in repaired if one is False)

    paid = [one for one in _group_values(values, "claim_amount_paid") if isinstance(one, int)]
    if paid:
        out["claim_amount_total"] = sum(paid)
        out["claim_amount_max"] = max(paid)

    incidents = [one for one in _group_values(values, "driver_incidents_three_years") if isinstance(one, int)]
    if incidents:
        out["max_driver_incidents"] = max(incidents)

    licences = _group_values(values, "driver_licence_status")
    if licences:
        out["suspended_licence_count"] = sum(1 for one in licences if one in ("suspended", "expired", "none"))

    coverage = values.get("current_dwelling_coverage")
    square_feet = values.get("square_feet")
    if isinstance(coverage, int) and isinstance(square_feet, int) and square_feet > 0:
        out["dwelling_coverage_per_sqft"] = round(coverage / square_feet, 2)

    state = values.get("property_state")
    if isinstance(state, str) and state.strip():
        # Spoken as "Ohio" as often as "OH", and the rule compares against codes.
        out["property_state_code"] = _state_code(state)

    return out


_STATE_NAMES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA",
    "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT",
    "virginia": "VA", "washington": "WA", "west virginia": "WV", "wisconsin": "WI",
    "wyoming": "WY", "district of columbia": "DC",
}


def _state_code(text: str) -> str:
    cleaned = text.strip().lower()
    if len(cleaned) == 2:
        return cleaned.upper()
    return _STATE_NAMES.get(cleaned, cleaned.upper()[:2])
