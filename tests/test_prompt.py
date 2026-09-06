"""The prompt. It has to carry the conduct rules and must NOT carry the form."""

from __future__ import annotations

from alderway_voice_agent.prompt import (
    INSTRUCTIONS,
    build_instructions,
    opening_line,
    voicemail_message,
)

CONTEXT = {
    "agent_name": "Avery",
    "company": "Alderway Insurance Services",
    "referral": "your mortgage lender",
    "lead_name": "Marguerite Halloway",
    "phone": "+16145550118",
    "partner_reference": "WM-4417",
    "property_hint": "2841 Wexford Lane, Dublin, Ohio 43017",
}


def test_every_placeholder_is_filled():
    rendered = build_instructions(CONTEXT)
    assert "{" not in rendered
    assert "Marguerite Halloway" in rendered
    assert "Alderway Insurance Services" in rendered


def test_missing_context_still_renders():
    rendered = build_instructions({})
    assert "{" not in rendered
    assert "unknown" in rendered


def test_the_prompt_forbids_pricing_and_advice():
    lowered = INSTRUCTIONS.lower()
    assert "never give a price" in lowered
    assert "never recommend cover" in lowered
    assert "not licensed to give it" in lowered


def test_the_prompt_forbids_taking_payment_details():
    lowered = INSTRUCTIONS.lower()
    assert "card number" in lowered
    assert "social security" in lowered


def test_the_prompt_requires_one_question_per_turn():
    assert "ONE question per turn" in INSTRUCTIONS


def test_the_prompt_puts_removal_before_everything_else():
    lowered = INSTRUCTIONS.lower()
    assert "honour_removal_request immediately" in lowered
    assert "never try to talk them out of it" in lowered


def test_the_prompt_says_eligibility_comes_from_the_tool():
    lowered = INSTRUCTIONS.lower()
    assert "until check_eligibility has told you" in lowered


def test_the_prompt_does_not_contain_the_questions():
    """The form lives in the tools API. A copy here is a copy that drifts and can be skipped."""
    from form_intake import INTAKE

    leaked = [one.ask for one in INTAKE.fields if one.ask.lower() in INSTRUCTIONS.lower()]
    assert leaked == []


def test_the_prompt_does_not_name_a_single_form_field():
    from form_intake import INTAKE

    named = [one.id for one in INTAKE.fields if one.id in INSTRUCTIONS]
    assert named == []


def test_the_prompt_names_every_tool_the_model_has_to_reach_for():
    for tool in (
        "get_next_question",
        "record_answers",
        "refuse_answer",
        "answer_unknown",
        "check_eligibility",
        "honour_removal_request",
        "schedule_callback",
        "transfer_to_licensed_agent",
        "end_call",
    ):
        assert tool in INSTRUCTIONS, tool


def test_the_opening_line_discloses_the_recording_before_anything_is_asked():
    line = opening_line(CONTEXT)
    assert "recorded line" in line
    assert line.index("recorded line") < line.index("Am I speaking")


def test_the_opening_line_names_the_company_and_the_reason():
    line = opening_line(CONTEXT)
    assert "Alderway Insurance Services" in line
    assert "mortgage lender" in line


def test_the_opening_line_works_without_a_name_on_file():
    line = opening_line({**CONTEXT, "lead_name": ""})
    assert "Am I speaking" not in line
    assert "Alderway Insurance Services" in line


def test_the_voicemail_message_names_the_company_and_says_nothing_personal():
    message = voicemail_message(CONTEXT)
    assert "Alderway Insurance Services" in message
    assert "Marguerite" not in message
    assert "2841" not in message
    assert "WM-4417" not in message


def test_the_voicemail_message_is_short_enough_for_a_mailbox():
    assert len(voicemail_message(CONTEXT).split()) < 45


def test_nothing_in_the_prompt_uses_an_em_dash():
    assert "\u2014" not in INSTRUCTIONS  # an em-dash, escaped so this file has none either
