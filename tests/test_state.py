"""State-machine tests. These are the guarantees the prompt only promises in words."""

from __future__ import annotations

import pytest
from alderway_voice_agent.state import GuardError, IntakeState


def _state(**kwargs) -> IntakeState:
    return IntakeState(
        lead_id="led_test",
        phone="+16145550118",
        session_id="ses_test",
        call_id="cal_test",
        **kwargs,
    )


def _consented() -> IntakeState:
    state = _state()
    state.note_settled("recording_disclosure_ack", "answered", True)
    state.note_settled("consent_to_continue", "answered", True)
    return state


# ------------------------------------------------------------ consent before collection


def test_collection_is_closed_until_both_consents_are_given():
    state = _state()
    assert state.collection_open is False
    with pytest.raises(GuardError):
        state.ensure_collection_allowed("year_built")


def test_the_recording_disclosure_alone_does_not_open_collection():
    state = _state()
    state.note_settled("recording_disclosure_ack", "answered", True)
    with pytest.raises(GuardError):
        state.ensure_collection_allowed("year_built")


def test_a_refused_consent_does_not_open_collection():
    state = _state()
    state.note_settled("recording_disclosure_ack", "answered", True)
    state.note_settled("consent_to_continue", "answered", False)
    assert state.collection_open is False
    with pytest.raises(GuardError):
        state.ensure_collection_allowed("roof_age_years")


def test_identity_questions_are_askable_before_consent():
    state = _state()
    state.ensure_collection_allowed("reached_right_party")
    state.ensure_collection_allowed("homeowner_confirmed")
    state.ensure_collection_allowed("consent_to_continue")


def test_consent_opens_the_rest_of_the_form():
    state = _consented()
    assert state.collection_open is True
    state.ensure_collection_allowed("year_built")


# ------------------------------------------------------------ never re-ask a settled field


def test_an_answered_field_is_never_asked_again():
    state = _consented()
    state.note_settled("year_built", "answered", 1998)
    with pytest.raises(GuardError) as caught:
        state.ensure_askable("year_built")
    assert "already recorded as answered" in str(caught.value)


def test_a_refused_field_is_never_asked_again():
    state = _consented()
    state.note_settled("current_premium_annual", "refused")
    with pytest.raises(GuardError):
        state.ensure_askable("current_premium_annual")


def test_a_field_answered_as_not_known_is_never_asked_again():
    state = _consented()
    state.note_settled("plumbing_material", "unknown")
    with pytest.raises(GuardError):
        state.ensure_askable("plumbing_material")


def test_an_unsettled_field_stays_askable():
    state = _consented()
    state.ensure_askable("square_feet")


def test_an_unknown_status_is_refused_outright():
    state = _consented()
    with pytest.raises(GuardError):
        state.note_settled("square_feet", "maybe")


# ------------------------------------------------------------ a refusal is recorded, not skipped


def test_a_refusal_must_be_on_the_record():
    state = _consented()
    with pytest.raises(GuardError):
        state.ensure_refusal_recorded("current_premium_annual")
    state.note_settled("current_premium_annual", "refused")
    state.ensure_refusal_recorded("current_premium_annual")


def test_an_answered_field_does_not_count_as_a_refusal():
    state = _consented()
    state.note_settled("current_premium_annual", "answered", 1850)
    with pytest.raises(GuardError):
        state.ensure_refusal_recorded("current_premium_annual")


# ------------------------------------------------------------ removal is honoured immediately


def test_a_removal_request_stops_every_further_question():
    state = _consented()
    state.mark_suppressed("caller asked to be removed")
    assert state.disposition == "removed"
    with pytest.raises(GuardError):
        state.ensure_askable("square_feet")
    with pytest.raises(GuardError):
        state.ensure_not_suppressed()


# ------------------------------------------------------------ no eligibility claim without a verdict


def test_nothing_may_be_said_about_eligibility_before_the_api_answers():
    state = _consented()
    assert state.verdict is None
    with pytest.raises(GuardError):
        state.ensure_verdict()


def test_a_verdict_without_a_decision_is_rejected():
    state = _consented()
    with pytest.raises(GuardError):
        state.set_verdict({"spoken_reason": "made up"})


def test_a_verdict_is_accepted_and_readable():
    state = _consented()
    state.set_verdict({"decision": "eligible", "soft": ["roof_ageing"], "spoken_reason": ""})
    assert state.ensure_verdict()["decision"] == "eligible"


def test_a_disqualified_intake_cannot_be_transferred():
    state = _consented()
    state.set_verdict(
        {"decision": "disqualified", "spoken_reason": "At that roof age none of the carriers will offer new cover."}
    )
    with pytest.raises(GuardError):
        state.ensure_transferable()


def test_an_eligible_intake_can_be_transferred():
    state = _consented()
    state.set_verdict({"decision": "eligible", "spoken_reason": ""})
    assert state.ensure_transferable()["decision"] == "eligible"


def test_only_a_reason_the_api_gave_may_be_spoken():
    state = _consented()
    state.set_verdict(
        {
            "decision": "disqualified",
            "spoken_reason": "At that roof age none of the carriers we work with will offer new cover.",
        }
    )
    assert state.may_say_reason(
        "I am sorry, at that roof age none of the carriers we work with will offer new cover."
    )
    assert not state.may_say_reason("Your premium would be about two thousand dollars.")


def test_no_reason_may_be_spoken_when_there_is_no_verdict():
    assert _consented().may_say_reason("anything at all") is False


# ------------------------------------------------------------ pacing


def test_settling_an_answer_clears_the_misheard_and_silence_counters():
    state = _consented()
    state.note_misheard()
    state.note_silence()
    state.note_settled("square_feet", "answered", 2400)
    assert state.misheard_streak == 0
    assert state.silence_prompts == 0


def test_outstanding_lists_what_was_asked_and_never_settled():
    state = _consented()
    state.note_asked("square_feet")
    state.note_asked("year_built")
    state.note_settled("year_built", "answered", 1998)
    assert state.outstanding() == ["square_feet"]
