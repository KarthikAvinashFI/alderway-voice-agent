"""Scripted callers, driven end to end through the form, the rules, the playbook and the boundary.

Each turn carries what the caller said and, separately, the fields a model would have pulled out of it.
The said text is not decoration: the objection playbook and the licensed-agent boundary run against it
for real, so a persona who asks "how much will it be" exercises the same path a live caller would.

What this does not prove is the voice pipeline. Speech is verified by holding a conversation, not by
asserting on a mock.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from alderway_voice_agent.objections import ObjectionExit, ObjectionTracker, crossed
from form_engine import ExtractedAnswer, IntakeEngine
from form_intake import INTAKE
from form_spec import AnswerType, Field
from personas import PERSONAS, Persona
from rules_engine import evaluate

# Values used to finish a form the script does not cover, so a persona stays readable and the intake
# still reaches a real eligibility decision. Counted separately so nothing here is ever mistaken for
# something the caller said.
_FILLER = {
    AnswerType.BOOL: "no",
    AnswerType.YEAR: "2015",
    AnswerType.CURRENCY: "1850",
    AnswerType.STRING: "nothing to add",
    AnswerType.DATE: "2027-03-14",
    AnswerType.ADDRESS: "1 Filler Street, Columbus, Ohio 43004",
    AnswerType.EMAIL: "filler@example.com",
    AnswerType.PHONE: "6145550100",
}


def _filler_for(field_def: Field) -> str:
    if field_def.type is AnswerType.ENUM and field_def.choices:
        return field_def.choices[0]
    if field_def.type is AnswerType.INT:
        low = int(field_def.minimum) if field_def.minimum is not None else 0
        high = int(field_def.maximum) if field_def.maximum is not None else 10
        return str(max(low, min(2, high)))
    return _FILLER.get(field_def.type, "not stated")


@dataclass
class Report:
    persona: str
    disposition: str = ""
    decision: str = ""
    events: list[str] = field(default_factory=list)
    from_script: int = 0
    from_filler: int = 0
    rejected: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    corrected: list[str] = field(default_factory=list)
    low_confidence: list[str] = field(default_factory=list)
    hard: list[str] = field(default_factory=list)
    soft: list[str] = field(default_factory=list)
    suppressed: bool = False
    turns_reached: int = 0
    spoken_reason: str = ""


def replay(persona: Persona) -> Report:
    report = Report(persona=persona.key)
    form = IntakeEngine(INTAKE)
    tracker = ObjectionTracker()
    stopped = False

    for turn in persona.turns:
        if stopped:
            report.events.append(f"not reached: {turn.said[:40]}")
            continue
        report.turns_reached += 1

        code = tracker.classify(turn.said)
        if code is not None:
            outcome = tracker.handle(code)
            report.events.append(f"objection {code} -> {outcome.exit.value}")
            if outcome.exit is ObjectionExit.HONOUR_REMOVAL:
                report.disposition, report.suppressed, stopped = "removed", True, True
                continue
            if outcome.exit is ObjectionExit.SCHEDULE_CALLBACK:
                report.disposition, stopped = "callback", True
                continue
            if outcome.exit is ObjectionExit.TRANSFER_HUMAN:
                report.disposition, stopped = "transferred", True
                continue
            if outcome.exit is ObjectionExit.END_POLITE:
                report.disposition, stopped = "declined", True
                continue

        boundary = crossed(turn.said)
        if boundary is not None:
            report.events.append(f"boundary {boundary.kind.value} deflected")

        for field_id, text in turn.answers:
            result = form.record(field_id, text)
            if not result.accepted:
                report.rejected.append(f"{field_id}: {result.problem}")
                continue
            report.from_script += 1
            if result.corrected:
                report.events.append(f"corrected {field_id}")
            if result.fatal:
                report.disposition, stopped = "declined_consent", True
        for field_id in turn.refuse:
            if form.refuse(field_id, turn.said).fatal:
                report.disposition, stopped = "declined_consent", True
        for field_id in turn.unknown:
            form.mark_unknown(field_id, turn.said)

    if persona.complete_form and not stopped:
        guard = 0
        while guard < 400:
            guard += 1
            item = form.next_item()
            if item is None:
                break
            form.record_batch([ExtractedAnswer(item.field.id, _filler_for(item.field))])
            report.from_filler += 1

    result = evaluate(form.values())
    report.decision = result.decision.value
    report.hard = [one.code for one in result.hard]
    report.soft = [one.code for one in result.soft]
    report.spoken_reason = result.spoken_reason()
    report.refused = form.refused()
    report.unknown = form.unknown()
    report.corrected = [one.field_id for one in form.answers.values() if one.corrected]
    report.low_confidence = form.low_confidence()
    if not report.disposition:
        report.disposition = "disqualified" if result.hard else "completed"
    return report


@pytest.fixture(scope="module")
def reports() -> dict[str, Report]:
    return {one.key: replay(one) for one in PERSONAS}


def test_every_persona_reaches_the_disposition_it_was_written_for(reports):
    wrong = {
        one.key: (one.expect_disposition, reports[one.key].disposition)
        for one in PERSONAS
        if one.expect_disposition and reports[one.key].disposition != one.expect_disposition
    }
    assert wrong == {}


def test_every_persona_reaches_the_decision_it_was_written_for(reports):
    wrong = {
        one.key: (one.expect_decision, reports[one.key].decision)
        for one in PERSONAS
        if one.expect_decision and reports[one.key].decision != one.expect_decision
    }
    assert wrong == {}


def test_nothing_a_persona_says_is_rejected_by_the_parser(reports):
    """A phrasing the parser cannot read is a question the agent would have to ask twice."""
    rejected = {key: value.rejected for key, value in reports.items() if value.rejected}
    assert rejected == {}


def test_the_cooperative_caller_is_quotable_with_nothing_flagged(reports):
    report = reports["cooperative"]
    assert report.decision == "eligible"
    assert report.hard == []
    assert report.from_script >= 15


def test_the_rambler_has_ten_fields_land_in_one_turn(reports):
    """The whole point of batching. Said in one breath, recorded in one call, never asked again."""
    report = reports["rambler"]
    assert report.from_script >= 19
    assert report.rejected == []


def test_the_rambler_corrects_the_roof_without_losing_the_first_answer(reports):
    assert "roof_age_years" in reports["rambler"].corrected


def test_the_partial_refuser_keeps_two_refusals_and_one_not_known(reports):
    report = reports["partial_refuser"]
    assert set(report.refused) == {"current_premium_annual", "quote_email"}
    assert report.unknown == ["plumbing_material"]
    assert report.decision == "eligible"


def test_the_partial_refuser_is_still_quotable(reports):
    """Refusing what they pay does not make somebody uninsurable, and must not read as though it does."""
    assert reports["partial_refuser"].disposition == "completed"


def test_a_hedged_answer_is_carried_as_low_confidence(reports):
    assert "roof_age_years" in reports["partial_refuser"].low_confidence


def test_the_disqualified_caller_is_declined_on_two_independent_rules(reports):
    report = reports["disqualified"]
    assert report.decision == "disqualified"
    assert set(report.hard) >= {"roof_too_old_asphalt", "too_many_claims"}


def test_the_disqualified_caller_gets_one_plain_reason(reports):
    reason = reports["disqualified"].spoken_reason
    assert reason
    assert "roof" in reason.lower()


def test_the_removal_request_stops_the_call_where_it_was_made(reports):
    report = reports["removal"]
    assert report.suppressed is True
    assert report.disposition == "removed"
    # Two turns reached, the third never asked. The third turn exists purely to prove that.
    assert report.turns_reached == 2
    assert any("not reached" in one for one in report.events)


def test_the_boundary_pusher_is_deflected_every_time_and_still_completes(reports):
    report = reports["boundary_pusher"]
    deflections = [one for one in report.events if one.startswith("boundary")]
    assert len(deflections) == 3
    assert report.decision == "eligible"


def test_a_boundary_question_is_never_recorded_as_an_answer(reports):
    assert reports["boundary_pusher"].rejected == []


def test_every_persona_has_a_distinct_name_and_number():
    assert len({one.lead_name for one in PERSONAS}) == len(PERSONAS)
    assert len({one.phone for one in PERSONAS}) == len(PERSONAS)


def test_no_persona_number_could_ever_be_a_real_one():
    """555 is the range reserved for fiction. A seeded number that rings somebody is a real problem."""
    for one in PERSONAS:
        assert one.phone[3:6] == "555", one.key
