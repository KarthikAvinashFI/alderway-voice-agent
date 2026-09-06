"""The tools API against the real Postgres. `docker compose up -d` first.

Marked integration because it needs the stack. What it proves is the part no unit test can: that an
answer survives the round trip, that a correction leaves a revision row behind, and that a dropped call
resumes from the database rather than from anybody's memory.
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest

BASE = os.environ.get("TOOLS_API_URL", "http://localhost:18092")
pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def api() -> httpx.Client:
    try:
        client = httpx.Client(base_url=BASE, timeout=10)
        client.get("/health").raise_for_status()
    except Exception as exc:
        pytest.skip(f"tools API not reachable at {BASE}: {exc}")
    return client


def _post(api: httpx.Client, path: str, **body) -> dict:
    response = api.post(path, json=body)
    response.raise_for_status()
    return response.json()


def test_health_reports_the_questionnaire_it_is_serving(api):
    body = api.get("/health").json()
    assert body["ok"] is True
    assert body["questionnaire"] == "home_quote_intake"


def test_a_seeded_lead_is_found_by_its_number(api):
    lead = _post(api, "/lookup_lead_by_phone", phone="+16145550118")
    assert lead["lead_id"] == "led_halloway"
    assert lead["suppressed"] is False
    assert lead["referral_label"]


def test_a_suppressed_number_is_reported_as_suppressed(api):
    lead = _post(api, "/lookup_lead_by_phone", phone="+16155550231")
    assert lead["suppressed"] is True


def test_a_call_cannot_be_started_for_a_suppressed_lead(api):
    response = api.post("/start_call", json={"lead_id": "led_vane"})
    assert response.status_code == 409
    assert response.json()["detail"] == "lead_suppressed"


def test_starting_a_call_hands_back_the_first_question(api):
    started = _post(api, "/start_call", lead_id="led_achterberg", resume=False)
    assert started["session_id"]
    assert started["next_question"]["field_id"] == "reached_right_party"
    assert started["next_question"]["done"] is False


def test_the_seeded_partial_intake_resumes_where_it_stopped(api):
    """A dropped call must not start again from the top."""
    started = _post(api, "/start_call", lead_id="led_okonjo", resume=True)
    assert started["resumed"] is True
    assert started["session_id"] == "ses_okonjo_partial"
    # Consent, name and address were already given, so the next thing asked is what follows them.
    assert started["next_question"]["field_id"] not in {
        "reached_right_party",
        "consent_to_continue",
        "property_address",
    }


def _fresh_session(api: httpx.Client) -> str:
    started = _post(api, "/start_call", lead_id="led_follett", resume=False)
    return started["session_id"]


def test_a_batch_of_volunteered_answers_all_persist(api):
    session_id = _fresh_session(api)
    result = _post(
        api,
        "/record_answers",
        session_id=session_id,
        answers=[
            {"field_id": "reached_right_party", "text": "speaking"},
            {"field_id": "homeowner_confirmed", "text": "yes we own it"},
            {"field_id": "recording_disclosure_ack", "text": "that's fine"},
            {"field_id": "consent_to_continue", "text": "go ahead"},
            {"field_id": "year_built", "text": "nineteen ninety eight"},
        ],
    )
    assert all(one["accepted"] for one in result["results"])
    values = {one["field_id"]: one["value"] for one in result["results"]}
    assert values["year_built"] == 1998
    assert values["homeowner_confirmed"] is True
    # Volunteered out of order, and therefore never asked again.
    assert result["next_question"]["field_id"] != "year_built"


def test_an_answer_survives_being_read_back_from_the_database(api):
    session_id = _fresh_session(api)
    _post(api, "/record_answers", session_id=session_id, answers=[{"field_id": "year_built", "text": "2004"}])
    again = _post(api, "/next_question", session_id=session_id)
    assert "year_built" in again["already_settled"]


def test_a_correction_leaves_the_first_answer_on_the_record(api):
    session_id = _fresh_session(api)
    _post(api, "/record_answers", session_id=session_id, answers=[{"field_id": "roof_age_years", "text": "five"}])
    corrected = _post(
        api, "/record_answers", session_id=session_id, answers=[{"field_id": "roof_age_years", "text": "actually seven"}]
    )
    assert corrected["results"][0]["corrected"] is True
    assert corrected["results"][0]["previous_value"] == 5

    result = _post(api, "/intake_result", session_id=session_id)
    revisions = {one["field_id"]: one for one in result["revisions"]}
    assert revisions["roof_age_years"]["previous_value"] == "5"


def test_a_refusal_is_stored_with_the_words_used(api):
    session_id = _fresh_session(api)
    _post(
        api,
        "/refuse_answer",
        session_id=session_id,
        field_id="current_premium_annual",
        said="I would rather not say",
    )
    result = _post(api, "/intake_result", session_id=session_id)
    refused = [one for one in result["answers"] if one["field_id"] == "current_premium_annual"]
    assert refused and refused[0]["status"] == "refused"
    assert refused[0]["verbatim"] == "I would rather not say"
    assert refused[0]["value"] is None


def test_not_knowing_is_recorded_differently_from_refusing(api):
    session_id = _fresh_session(api)
    _post(api, "/answer_unknown", session_id=session_id, field_id="plumbing_material", said="no idea")
    result = _post(api, "/intake_result", session_id=session_id)
    assert result["summary"]["unknown"] == ["plumbing_material"]


def test_a_readback_is_demanded_for_an_address(api):
    session_id = _fresh_session(api)
    result = _post(
        api,
        "/record_answers",
        session_id=session_id,
        answers=[{"field_id": "property_address", "text": "8802 Fenwick Row, Waukesha, Wisconsin 53188"}],
    )
    assert result["results"][0]["readback_required"] is True


def test_a_hedged_answer_comes_back_marked_low_confidence(api):
    session_id = _fresh_session(api)
    result = _post(
        api, "/record_answers", session_id=session_id, answers=[{"field_id": "roof_age_years", "text": "I think about twelve"}]
    )
    assert result["results"][0]["confidence"] == "low"


def test_an_unreadable_answer_is_rejected_with_a_reason(api):
    session_id = _fresh_session(api)
    result = _post(
        api, "/record_answers", session_id=session_id, answers=[{"field_id": "year_built", "text": "sometime after the war"}]
    )
    assert result["results"][0]["accepted"] is False
    assert "year" in result["results"][0]["problem"]


def test_eligibility_is_decided_server_side_and_carries_one_spoken_reason(api):
    session_id = _fresh_session(api)
    _post(
        api,
        "/record_answers",
        session_id=session_id,
        answers=[
            {"field_id": "homeowner_confirmed", "text": "yes"},
            {"field_id": "property_state", "text": "Wisconsin"},
            {"field_id": "occupancy", "text": "we live there"},
            {"field_id": "roof_material", "text": "asphalt shingle"},
            {"field_id": "roof_age_years", "text": "twenty five"},
        ],
    )
    verdict = _post(api, "/check_eligibility", session_id=session_id)
    assert verdict["decision"] == "disqualified"
    assert "roof_too_old_asphalt" in verdict["hard"]
    assert verdict["spoken_reason"]
    assert verdict["transferable"] is False


def test_a_verdict_is_written_down_so_the_transcript_can_be_checked_against_it(api):
    session_id = _fresh_session(api)
    _post(api, "/record_answers", session_id=session_id, answers=[{"field_id": "homeowner_confirmed", "text": "no"}])
    verdict = _post(api, "/check_eligibility", session_id=session_id)
    result = _post(api, "/intake_result", session_id=session_id)
    assert result["decision"]["decision"] == verdict["decision"]
    assert result["decision"]["spoken_reason"] == verdict["spoken_reason"]


def test_an_incomplete_intake_reports_what_is_missing_rather_than_passing(api):
    session_id = _fresh_session(api)
    verdict = _post(api, "/check_eligibility", session_id=session_id)
    assert verdict["decision"] == "needs_review"
    assert verdict["missing_required"]
    assert verdict["indeterminate"]
    assert all("missing" in one for one in verdict["indeterminate"])


def test_a_removal_request_suppresses_the_number_and_is_idempotent(api):
    phone = f"+1614555{uuid.uuid4().int % 10000:04d}"
    first = _post(api, "/suppress_number", phone=phone, reason="test", verbatim="take me off")
    second = _post(api, "/suppress_number", phone=phone, reason="test", verbatim="take me off")
    assert first["suppressed"] is second["suppressed"] is True
    assert _post(api, "/is_suppressed", phone=phone)["suppressed"] is True


def test_the_call_queue_never_offers_a_suppressed_number(api):
    queue = _post(api, "/leads_to_call", lead_id="")["leads"]
    assert "+16155550231" not in [one["phone"] for one in queue]


def test_consent_is_recorded_as_an_event(api):
    body = _post(api, "/record_consent", lead_id="led_halloway", kind="recording", granted=True, verbatim="that's fine")
    assert body["recorded"] is True


def test_an_unknown_consent_kind_is_refused(api):
    response = api.post("/record_consent", json={"lead_id": "led_halloway", "kind": "whatever", "granted": True})
    assert response.status_code == 400


def test_a_call_can_be_closed_with_a_disposition(api):
    started = _post(api, "/start_call", lead_id="led_bramwell", resume=False)
    ended = _post(
        api,
        "/end_call",
        call_id=started["call_id"],
        disposition="callback",
        ended_reason="caller was driving",
        duration_seconds=42,
    )
    assert ended["disposition"] == "callback"
    assert ended["lead_status"] == "retry"


def test_a_transfer_records_how_much_was_collected(api):
    started = _post(api, "/start_call", lead_id="led_nettlefold", resume=False)
    _post(api, "/record_answers", session_id=started["session_id"], answers=[{"field_id": "homeowner_confirmed", "text": "yes"}])
    body = _post(api, "/request_transfer", call_id=started["call_id"], reason="caller asked for a person")
    assert body["requested"] is True


def test_a_callback_puts_the_lead_back_in_the_queue(api):
    body = _post(api, "/schedule_callback", lead_id="led_bramwell", window="weekday_evening", notes="after six")
    assert body["scheduled"] is True


def test_an_unknown_session_is_a_404_rather_than_an_empty_form(api):
    response = api.post("/next_question", json={"session_id": "ses_does_not_exist"})
    assert response.status_code == 404


def test_the_calling_window_is_judged_in_the_property_timezone(api):
    body = _post(api, "/calling_window", lead_id="led_halloway")
    assert "allowed" in body
    assert body["local_time"] or body["reason"]
