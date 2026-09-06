"""Eligibility rules: what one is, and every one we have.

Rules are data for the same reason the questions are: underwriting appetite changes weekly, the people
who change it are not engineers, and a declined caller has a right to a reason that was written down
before the call rather than improvised during it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from form_intake import LICENSED_STATES
from form_spec import All, Any_, Condition, Predicate


class Severity(str, Enum):
    # No carrier on the panel will look at it. Close the call politely, do not transfer.
    HARD = "hard"
    # Quotable, but the licensed agent needs to know before they open their mouth.
    SOFT = "soft"


class Decision(str, Enum):
    ELIGIBLE = "eligible"
    # A hard rule could not be decided, usually because the answer it needs was refused. A human
    # decides, and the record says exactly what is missing.
    NEEDS_REVIEW = "needs_review"
    DISQUALIFIED = "disqualified"


@dataclass(frozen=True)
class Rule:
    code: str
    severity: Severity
    # Said to the caller where it is the reason a call ends, so it has to be plain and not blame them.
    reason: str
    predicate: Predicate
    # Written on the record for the licensed agent rather than spoken.
    internal_note: str = ""


@dataclass(frozen=True)
class Finding:
    code: str
    severity: Severity
    reason: str
    internal_note: str = ""
    # Populated only on an undecidable rule: what would have to be known to settle it.
    missing: tuple[str, ...] = ()


@dataclass(frozen=True)
class EligibilityResult:
    decision: Decision
    hard: tuple[Finding, ...] = ()
    soft: tuple[Finding, ...] = ()
    indeterminate: tuple[Finding, ...] = ()

    @property
    def transferable(self) -> bool:
        return self.decision is not Decision.DISQUALIFIED

    def spoken_reason(self) -> str:
        """The one thing to say to a caller who cannot be quoted."""
        return self.hard[0].reason if self.hard else ""

    def as_dict(self) -> dict[str, object]:
        return {
            "decision": self.decision.value,
            "hard": [one.code for one in self.hard],
            "soft": [one.code for one in self.soft],
            "indeterminate": [one.code for one in self.indeterminate],
            "notes": [one.internal_note for one in (*self.hard, *self.soft) if one.internal_note],
        }


# ---------------------------------------------------------------- thresholds
# Carriers on the panel decline an asphalt roof past this, and give the harder materials longer.
ASPHALT_ROOF_LIMIT_YEARS = 20
OTHER_ROOF_LIMIT_YEARS = 40
ROOF_AGEING_YEARS = 15
CLAIMS_LIMIT = 3
LAPSE_LIMIT_DAYS = 60
VACANCY_LIMIT_MONTHS = 6


# A rule whose inputs sit behind a branch has to name the gate as well as the value. Without it the
# predicate is undecidable whenever the branch is shut, and an undecidable HARD rule sends the intake to
# manual review: a house with no claims, no dogs and modern wiring failed three rules that way at once.
RULES: tuple[Rule, ...] = (
    Rule(
        "renter_not_homeowner",
        Severity.HARD,
        "This one is for homeowners, so a home policy is not the right product here.",
        Condition("homeowner_confirmed", "eq", False),
        internal_note="Renter. Route to a renters product rather than declining outright.",
    ),
    Rule(
        "outside_licensed_states",
        Severity.HARD,
        "We are not licensed in that state yet, so I am not able to quote it.",
        Condition("property_state_code", "not_in", LICENSED_STATES),
        internal_note="Property outside the licensed footprint.",
    ),
    Rule(
        "vacant_property",
        Severity.HARD,
        "An empty property needs a specialist vacancy policy, which is not something we place.",
        Condition("occupancy", "eq", "vacant"),
    ),
    Rule(
        "vacant_long_term",
        Severity.HARD,
        "A property empty that long needs a specialist policy.",
        All((Condition("occupancy", "eq", "vacant"), Condition("vacant_since_months", "gte", VACANCY_LIMIT_MONTHS))),
        internal_note="Long term vacancy, distinct from a short gap between tenants.",
    ),
    Rule(
        "roof_too_old_asphalt",
        Severity.HARD,
        "At that roof age none of the carriers we work with will offer new cover, and a replacement would change that.",
        All(
            (
                Condition("roof_material", "in", ("asphalt_shingle", "architectural_shingle")),
                Condition("roof_age_years", "gt", ASPHALT_ROOF_LIMIT_YEARS),
            )
        ),
    ),
    Rule(
        "roof_too_old_other",
        Severity.HARD,
        "The roof is past the age the carriers will write, so a quote would not stand.",
        All(
            (
                Condition("roof_material", "in", ("metal", "tile", "slate", "flat_membrane")),
                Condition("roof_age_years", "gt", OTHER_ROOF_LIMIT_YEARS),
            )
        ),
    ),
    Rule(
        "too_many_claims",
        Severity.HARD,
        "With that many claims in five years the carriers on our panel will not look at it yet.",
        All((Condition("claims_last_five_years", "eq", True), Condition("claims_count", "gte", CLAIMS_LIMIT))),
    ),
    Rule(
        "cancelled_for_non_payment",
        Severity.HARD,
        "A cancellation for non payment has to age off before carriers will consider new cover.",
        All((Condition("prior_cancellation", "eq", True), Condition("cancellation_reason", "eq", "non_payment"))),
    ),
    Rule(
        "restricted_dog_breed",
        Severity.HARD,
        "That breed is excluded by the carriers we place liability with, so I cannot get this quoted.",
        All((Condition("dogs", "eq", True), Condition("dog_breed_restricted", "eq", True))),
    ),
    Rule(
        "commercial_exposure",
        Severity.HARD,
        "With staff and customers on site this needs a commercial policy rather than a home one.",
        All(
            (
                Condition("business_at_home", "eq", True),
                Condition("business_employees_onsite", "gte", 3),
                Condition("business_client_visits", "eq", True),
            )
        ),
        internal_note="Home business with employees and foot traffic. Refer to commercial lines.",
    ),
    Rule(
        "wood_stove_uninspected",
        Severity.HARD,
        "Carriers need a wood burner inspected within the year before they will write the house.",
        All(
            (
                Condition("secondary_heat", "in", ("wood_stove", "pellet_stove")),
                Condition("wood_stove_inspected", "eq", False),
            )
        ),
    ),
    Rule(
        "coverage_lapse",
        Severity.HARD,
        "A gap in cover that long has to be bridged before a standard carrier will take it on.",
        All((Condition("currently_insured", "eq", False), Condition("coverage_lapse_days", "gt", LAPSE_LIMIT_DAYS))),
    ),
    Rule(
        "short_term_rental",
        Severity.HARD,
        "Short term letting is excluded on the home policies we place.",
        All((Condition("occupancy", "eq", "rental"), Condition("rental_short_term_platform", "eq", True))),
    ),
    Rule(
        "dog_bite_history",
        Severity.HARD,
        "A previous bite makes the liability side uninsurable on this panel.",
        All((Condition("dogs", "eq", True), Condition("dog_bite_history", "eq", True))),
    ),
    Rule(
        "knob_and_tube_wiring",
        Severity.HARD,
        "Live knob and tube wiring is declined across the panel until it is replaced.",
        All((Condition("year_built", "lt", 1960), Condition("electrical_knob_and_tube", "eq", True))),
    ),
    Rule(
        "roof_ageing",
        Severity.SOFT,
        "",
        All(
            (
                Condition("roof_age_years", "gt", ROOF_AGEING_YEARS),
                Condition("roof_age_years", "lte", ASPHALT_ROOF_LIMIT_YEARS),
            )
        ),
        internal_note="Roof is ageing. Expect a roof surcharge or an actual cash value settlement clause.",
    ),
    Rule(
        "claims_moderate",
        Severity.SOFT,
        "",
        All((Condition("claims_count", "gte", 1), Condition("claims_count", "lt", CLAIMS_LIMIT))),
        internal_note="One or two claims in five years. Fewer carriers, expect loaded pricing.",
    ),
    Rule(
        "repeat_water_claims",
        Severity.SOFT,
        "",
        Condition("water_claim_count", "gte", 2),
        internal_note="Repeat water losses. Ask about the cause before quoting.",
    ),
    Rule(
        "unrepaired_damage",
        Severity.SOFT,
        "",
        Condition("unrepaired_claim_count", "gte", 1),
        internal_note="Damage from a paid claim is unrepaired. Cover will exclude it until proof of repair.",
    ),
    Rule(
        "prior_cancellation_other",
        Severity.SOFT,
        "",
        All(
            (
                Condition("prior_cancellation", "eq", True),
                Condition("cancellation_reason", "not_in", ("non_payment",)),
            )
        ),
        internal_note="Prior cancellation for something other than payment. Needs the carrier's appetite checked.",
    ),
    Rule(
        "unfenced_pool",
        Severity.SOFT,
        "",
        All((Condition("pool", "eq", True), Condition("pool_fenced", "eq", False))),
        internal_note="Unfenced pool. Most carriers require a fence and a latching gate as a condition.",
    ),
    Rule(
        "pool_diving_board_present",
        Severity.SOFT,
        "",
        All((Condition("pool", "eq", True), Condition("pool_diving_board", "eq", True))),
        internal_note="Diving board or slide. A few carriers exclude it outright.",
    ),
    Rule(
        "trampoline_present",
        Severity.SOFT,
        "",
        Condition("trampoline", "eq", True),
        internal_note="Trampoline on site. Liability may be limited.",
    ),
    Rule(
        "wood_shake_roof",
        Severity.SOFT,
        "",
        Condition("roof_material", "eq", "wood_shake"),
        internal_note="Wood shake roof. Wildfire scoring applies and the panel narrows.",
    ),
    Rule(
        "polybutylene_plumbing",
        Severity.SOFT,
        "",
        Condition("plumbing_material", "eq", "polybutylene"),
        internal_note="Polybutylene supply lines. Water damage exclusions are likely.",
    ),
    Rule(
        "buried_oil_tank",
        Severity.SOFT,
        "",
        Condition("oil_tank_location", "eq", "underground"),
        internal_note="Buried oil tank. Pollution liability is excluded and some carriers decline.",
    ),
    Rule(
        "far_from_protection",
        Severity.SOFT,
        "",
        Any_((Condition("fire_station_miles", "gt", 5), Condition("hydrant_distance_feet", "gt", 1000))),
        internal_note="Protection class will be poor, which drives the premium.",
    ),
    Rule(
        "old_home_no_updates",
        Severity.SOFT,
        "",
        All((Condition("home_age", "gte", 60), Condition("years_since_electrical_update", "gte", 40))),
        internal_note="Old house with no recent electrical update. Expect an inspection requirement.",
    ),
    Rule(
        "dwelling_coverage_low",
        Severity.SOFT,
        "",
        All((Condition("dwelling_coverage_per_sqft", "lt", 100), Condition("current_dwelling_coverage", "gt", 0))),
        internal_note="Dwelling limit looks low against the square footage. Replacement cost needs rerunning.",
    ),
    Rule(
        "suspended_licence",
        Severity.SOFT,
        "",
        Condition("suspended_licence_count", "gte", 1),
        internal_note="A driver is not currently licensed. Auto side needs them excluded or rated.",
    ),
    Rule(
        "driver_incidents_high",
        Severity.SOFT,
        "",
        Condition("max_driver_incidents", "gte", 3),
        internal_note="Three or more incidents on one driver in three years. Non standard auto market.",
    ),
    Rule(
        "no_smoke_detectors",
        Severity.SOFT,
        "",
        Condition("smoke_detectors", "eq", False),
        internal_note="No working smoke detectors. Usually a condition of binding rather than a decline.",
    ),
)

HARD_CODES = tuple(one.code for one in RULES if one.severity is Severity.HARD)
SOFT_CODES = tuple(one.code for one in RULES if one.severity is Severity.SOFT)


def rule(code: str) -> Rule | None:
    for one in RULES:
        if one.code == code:
            return one
    return None
