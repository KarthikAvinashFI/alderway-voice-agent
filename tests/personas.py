"""Scripted callers, for proving the form and the rules without spending a phone call.

Each turn carries what the caller actually said and, separately, the fields a model would have pulled
out of it. The said text is not decoration: the objection playbook and the licensed agent boundary are
run against it for real, so a persona who says "how did you get my number" exercises the same path a
live caller would.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Turn:
    said: str
    # (field_id, text) pairs, exactly as the model would hand them to record_answers.
    answers: tuple[tuple[str, str], ...] = ()
    refuse: tuple[str, ...] = ()
    unknown: tuple[str, ...] = ()


@dataclass(frozen=True)
class Persona:
    key: str
    description: str
    lead_name: str
    phone: str
    time_zone: str
    turns: tuple[Turn, ...]
    # What the replay asserts. Written down so a persona is a test rather than a demonstration.
    expect_decision: str = ""
    expect_disposition: str = ""
    # Whether the replay should fill in the fields the script does not cover.
    complete_form: bool = True
    notes: str = ""


COOPERATIVE = Persona(
    key="cooperative",
    description="Answers what is asked, one thing at a time, and can be quoted.",
    lead_name="Marguerite Halloway",
    phone="6145550118",
    time_zone="America/New_York",
    expect_decision="eligible",
    expect_disposition="completed",
    turns=(
        Turn("Yes, speaking.", (("reached_right_party", "homeowner"),)),
        Turn("Yes, we own it.", (("homeowner_confirmed", "yes"),)),
        Turn("That's fine.", (("recording_disclosure_ack", "yes"),)),
        Turn("Sure, I've got a few minutes.", (("consent_to_continue", "yes"),)),
        Turn("Marguerite Halloway.", (("contact_name", "Marguerite Halloway"),)),
        Turn(
            "It's 2841 Wexford Lane, Dublin, Ohio, 43017.",
            (("property_address", "2841 Wexford Lane, Dublin, Ohio 43017"), ("property_state", "Ohio")),
        ),
        Turn("It's where we live, yes.", (("occupancy", "primary"),)),
        Turn("Nineteen ninety eight.", (("year_built", "nineteen ninety eight"),)),
        Turn("About two thousand four hundred square feet.", (("square_feet", "about two thousand four hundred"),)),
        Turn("Brick veneer.", (("construction_type", "brick veneer"),)),
        Turn("Roof was done six years ago, architectural shingle.", (("roof_age_years", "six"), ("roof_material", "architectural shingle"))),
        Turn("No, we've never claimed.", (("claims_last_five_years", "no"),)),
        Turn("No, nothing like that.", (("prior_cancellation", "no"),)),
        Turn("Yes we do have insurance.", (("currently_insured", "yes"),)),
        Turn("No thanks, just the house for now.", (("bundle_interest", "no"),)),
    ),
)

RAMBLER = Persona(
    key="rambler",
    description="Volunteers half the form in one breath, out of order, then corrects a value.",
    lead_name="Desmond Achterberg",
    phone="4695550473",
    time_zone="America/Chicago",
    expect_decision="eligible",
    expect_disposition="completed",
    notes="Proves batch application and that nothing volunteered is asked again.",
    turns=(
        Turn("Yes that's me, and yes I own it, go ahead.", (("reached_right_party", "homeowner"), ("homeowner_confirmed", "yes"))),
        Turn("Recording's fine, and yes I've got time.", (("recording_disclosure_ack", "yes"), ("consent_to_continue", "yes"))),
        Turn("Desmond Achterberg.", (("contact_name", "Desmond Achterberg"),)),
        Turn(
            "Right, so it's 1190 Calloway Bend, Plano, Texas, 75024. Built in 2004, about thirty two "
            "hundred square feet, it's a frame house, we live there, roof was replaced in 2021 so "
            "that's about five years, asphalt shingle, no claims ever, and no I've never been cancelled.",
            (
                ("property_address", "1190 Calloway Bend, Plano, Texas 75024"),
                ("property_state", "Texas"),
                ("year_built", "2004"),
                ("square_feet", "about thirty two hundred"),
                ("construction_type", "frame"),
                ("occupancy", "primary"),
                ("roof_age_years", "five"),
                ("roof_material", "asphalt shingle"),
                ("claims_last_five_years", "no"),
                ("prior_cancellation", "no"),
            ),
        ),
        Turn("Actually hang on, the roof was 2019, not 2021. So seven years.", (("roof_age_years", "seven"),)),
        Turn("Yes, insured at the moment.", (("currently_insured", "yes"),)),
        Turn("Go on then, do the cars as well.", (("bundle_interest", "yes"),)),
        Turn("Two cars.", (("vehicle_count", "two"),)),
        Turn("Two drivers.", (("driver_count", "two"),)),
    ),
)

PARTIAL_REFUSER = Persona(
    key="partial_refuser",
    description="Co-operates on the property, refuses money and email, does not know the plumbing.",
    lead_name="Ottoline Bramwell",
    phone="3175550692",
    time_zone="America/Indiana/Indianapolis",
    expect_decision="eligible",
    expect_disposition="completed",
    notes="Proves a refusal is stored with the words used, and that refusing twice is never asked.",
    turns=(
        Turn("Yes, this is she.", (("reached_right_party", "homeowner"), ("homeowner_confirmed", "yes"))),
        Turn("I suppose so.", (("recording_disclosure_ack", "yes"), ("consent_to_continue", "yes"))),
        Turn("Ottoline Bramwell.", (("contact_name", "Ottoline Bramwell"),)),
        Turn("How did you get my number?", ()),
        Turn(
            "Fine. 664 Harlow Street, Carmel, Indiana, 46032.",
            (("property_address", "664 Harlow Street, Carmel, Indiana 46032"), ("property_state", "Indiana")),
        ),
        Turn("We live there. Built 1974, about eighteen hundred feet, masonry.",
             (("occupancy", "primary"), ("year_built", "1974"), ("square_feet", "eighteen hundred"), ("construction_type", "masonry"))),
        Turn("Roof is maybe twelve years, metal.", (("roof_age_years", "maybe twelve"), ("roof_material", "metal"))),
        Turn("I would rather not say what I pay.", (), refuse=("current_premium_annual",)),
        Turn("I honestly don't know what the pipes are.", (), unknown=("plumbing_material",)),
        Turn("I don't give my email out over the phone.", (), refuse=("quote_email",)),
        Turn("No claims, and never cancelled.", (("claims_last_five_years", "no"), ("prior_cancellation", "no"))),
        Turn("Yes, I have cover now.", (("currently_insured", "yes"),)),
        Turn("No cars, thank you.", (("bundle_interest", "no"),)),
    ),
)

DISQUALIFIED = Persona(
    key="disqualified",
    description="Old asphalt roof and three claims. Cannot be quoted, and has to be told once and let go.",
    lead_name="Corwin Nettlefold",
    phone="7025550845",
    time_zone="America/Los_Angeles",
    expect_decision="disqualified",
    expect_disposition="disqualified",
    complete_form=False,
    notes="Two independent hard rules, so it also proves the first reason is the one spoken.",
    turns=(
        Turn("Yeah that's me, I own the place.", (("reached_right_party", "homeowner"), ("homeowner_confirmed", "yes"))),
        Turn("Fine, and yes go ahead.", (("recording_disclosure_ack", "yes"), ("consent_to_continue", "yes"))),
        Turn("Corwin Nettlefold.", (("contact_name", "Corwin Nettlefold"),)),
        Turn(
            "3306 Ridgemount Drive, Knoxville, Tennessee, 37919. Lived there since the nineties.",
            (("property_address", "3306 Ridgemount Drive, Knoxville, Tennessee 37919"), ("property_state", "Tennessee"), ("occupancy", "primary")),
        ),
        Turn("Built 1991, twenty six hundred square feet, frame.",
             (("year_built", "1991"), ("square_feet", "twenty six hundred"), ("construction_type", "frame"))),
        Turn("The roof is original, so about twenty five years. Asphalt shingle.",
             (("roof_age_years", "twenty five"), ("roof_material", "asphalt shingle"))),
        Turn("We've claimed three times, hail twice and a burst pipe.",
             (("claims_last_five_years", "yes"), ("claims_count", "three"))),
    ),
)

REMOVAL = Persona(
    key="removal",
    description="Asks to be taken off the list on the second turn.",
    lead_name="Perpetua Vane",
    phone="6155550231",
    time_zone="America/Chicago",
    expect_disposition="removed",
    complete_form=False,
    notes="Honoured immediately, nothing asked afterwards, number suppressed.",
    turns=(
        Turn("Yes?", (("reached_right_party", "homeowner"),)),
        Turn("Take me off your list and don't call me again.", ()),
        Turn("This turn must never be reached.", (("contact_name", "should not be recorded"),)),
    ),
)

BOUNDARY_PUSHER = Persona(
    key="boundary_pusher",
    description="Keeps asking for a price and for advice, which the agent may not give.",
    lead_name="Ignatius Follett",
    phone="4145550377",
    time_zone="America/Chicago",
    expect_decision="eligible",
    expect_disposition="completed",
    notes="Every boundary question must be deflected and none of them recorded as an answer.",
    turns=(
        Turn("Speaking, and it's my house.", (("reached_right_party", "homeowner"), ("homeowner_confirmed", "yes"))),
        Turn("Alright, go on.", (("recording_disclosure_ack", "yes"), ("consent_to_continue", "yes"))),
        Turn("Ignatius Follett.", (("contact_name", "Ignatius Follett"),)),
        Turn("So how much will it be, roughly?", ()),
        Turn("8802 Fenwick Row, Waukesha, Wisconsin, 53188.",
             (("property_address", "8802 Fenwick Row, Waukesha, Wisconsin 53188"), ("property_state", "Wisconsin"))),
        Turn("What deductible should I get?", ()),
        Turn("Our place, built 2011, two thousand square feet, frame.",
             (("occupancy", "primary"), ("year_built", "2011"), ("square_feet", "two thousand"), ("construction_type", "frame"))),
        Turn("Roof is four years old, architectural shingle. Am I covered for flooding?",
             (("roof_age_years", "four"), ("roof_material", "architectural shingle"))),
        Turn("No claims, never cancelled, and yes I'm insured.",
             (("claims_last_five_years", "no"), ("prior_cancellation", "no"), ("currently_insured", "yes"))),
        Turn("No cars.", (("bundle_interest", "no"),)),
    ),
)

PERSONAS: tuple[Persona, ...] = (
    COOPERATIVE,
    RAMBLER,
    PARTIAL_REFUSER,
    DISQUALIFIED,
    REMOVAL,
    BOUNDARY_PUSHER,
)


def persona(key: str) -> Persona | None:
    for one in PERSONAS:
        if one.key == key:
            return one
    return None
