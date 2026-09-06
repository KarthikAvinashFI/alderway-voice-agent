"""Tools API, the only authority on what to ask next and on whether an intake can be quoted.

Every endpoint mirrors one agent tool. The direction of trust is the point. The agent does not hold the
form, so it cannot skip a question or ask a settled one twice; it does not hold the rules, so it cannot
decide somebody is uninsurable; and it does not hold the answers, so a dropped call leaves them here
rather than in a model's context. Any decline reason the agent speaks has to appear in a response from
this service, which is what makes "it did not invent that" checkable from the transcript.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import date, datetime
from typing import Any

import logging
import psycopg
from fastapi import FastAPI, HTTPException
from form_engine import Answer, IntakeEngine, engine_from_records
from form_spec import AnswerStatus, Confidence
from form_intake import INTAKE
from psycopg.rows import dict_row
from psycopg.types.json import Json
from pydantic import BaseModel, Field
from rules_engine import evaluate

DSN = os.environ.get(
    "DATABASE_URL", "postgresql://alderway:alderway@postgres:5432/alderway_demo"
)
app = FastAPI(title="Alderway Insurance Services intake tools", version="1.0.0")

# Read back before moving on. Only these, because confirming everything doubles the length of a call
# and teaches the caller to stop listening.
READBACK_TYPES = {"address", "email", "currency", "date", "phone"}
READBACK_FIELDS = {"property_address", "quote_email", "callback_number_confirm", "contact_name"}
# How many questions ahead the agent is shown. Enough that a volunteered answer has an id to land on,
# few enough that the model still asks them one at a time.
UPCOMING_FIELDS = 8


def db():
    return psycopg.connect(DSN, row_factory=dict_row)


def one(sql: str, params: tuple = ()) -> dict | None:
    with db() as c, c.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def many(sql: str, params: tuple = ()) -> list[dict]:
    with db() as c, c.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def run(sql: str, params: tuple = ()) -> None:
    with db() as c, c.cursor() as cur:
        cur.execute(sql, params)
        c.commit()


log = logging.getLogger("uvicorn.error")

# Every table this service writes to. Checked once at startup, because a rebuilt world that is missing
# one fails at the first call that touches it, and the traceback names the table but nothing says the
# world was built short.
DECLARED_TABLES = (
    "lender_partners", "campaigns", "leads", "do_not_call", "intake_sessions", "call_attempts",
    "answers", "answer_revisions", "consent_events", "eligibility_decisions", "transfers",
    "callback_requests", "audit_log",
)


@app.on_event("startup")
def report_missing_tables() -> None:
    try:
        present = {
            row["table_name"]
            for row in many(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
            )
        }
    except Exception:
        log.exception("could not read the schema at startup")
        return
    missing = [name for name in DECLARED_TABLES if name not in present]
    if missing:
        log.error("world is missing %d declared table(s): %s", len(missing), ", ".join(missing))
    else:
        log.info("all %d declared tables present", len(DECLARED_TABLES))


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def audit(entity: str, entity_id: str, action: str, detail: dict | None = None) -> None:
    """Record what happened. Never the reason a call fails.

    The audit trail is a record OF the call, not a precondition FOR it, so a write that cannot land is
    logged loudly and the call continues. Dropping somebody's call because a log row failed is the
    worse of the two outcomes, and one rebuilt world was missing this table entirely, which killed
    every call at `start_call`.
    """
    try:
        run(
            "INSERT INTO audit_log (audit_id, entity, entity_id, action, detail)"
            " VALUES (%s, %s, %s, %s, %s::jsonb)",
            (new_id("aud"), entity, entity_id, action, json.dumps(detail or {})),
        )
    except Exception:
        log.exception("audit row not written: %s %s %s", entity, entity_id, action)


def _d(value) -> str | None:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, (date, datetime)) else str(value)


# ---------------------------------------------------------------- answer encoding


def encode_value(value: Any) -> tuple[str | None, str]:
    if value is None:
        return None, ""
    if isinstance(value, bool):
        return str(value), "bool"
    if isinstance(value, int):
        return str(value), "int"
    if isinstance(value, float):
        return str(value), "float"
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat(), "date"
    return str(value), "str"


def decode_value(text: str | None, value_type: str) -> Any:
    if text is None:
        return None
    if value_type == "bool":
        return text == "True"
    if value_type == "int":
        return int(text)
    if value_type == "float":
        return float(text)
    if value_type == "date":
        return date.fromisoformat(text)
    return text


def load_engine(session_id: str) -> IntakeEngine:
    """Rebuild the form from the rows. Stateless on purpose, so two calls cannot disagree."""
    rows = many(
        "SELECT field_id, value_text, value_type, status, verbatim, confidence, sequence"
        " FROM answers WHERE session_id = %s ORDER BY sequence",
        (session_id,),
    )
    records = [
        {
            "field_id": row["field_id"],
            "value": decode_value(row["value_text"], row["value_type"]),
            "status": row["status"],
            "verbatim": row["verbatim"],
            "confidence": row["confidence"],
            "sequence": row["sequence"],
        }
        for row in rows
    ]
    return engine_from_records(INTAKE, records)


def persist(session_id: str, answer: Answer) -> None:
    """Upsert one answer, moving whatever was there into the revision table first."""
    value_text, value_type = encode_value(answer.value)
    existing = one(
        "SELECT answer_id, value_text, value_type, status, verbatim, confidence, sequence"
        " FROM answers WHERE session_id = %s AND field_id = %s",
        (session_id, answer.field_id),
    )
    if existing is None:
        run(
            "INSERT INTO answers (answer_id, session_id, field_id, value_text, value_type, status,"
            " verbatim, confidence, sequence) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                new_id("ans"),
                session_id,
                answer.field_id,
                value_text,
                value_type,
                answer.status.value,
                answer.verbatim,
                answer.confidence.value,
                answer.sequence,
            ),
        )
        return
    unchanged = existing["value_text"] == value_text and existing["status"] == answer.status.value
    if unchanged:
        return
    run(
        "INSERT INTO answer_revisions (revision_id, answer_id, value_text, value_type, status,"
        " verbatim, confidence, sequence) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            new_id("rev"),
            existing["answer_id"],
            existing["value_text"],
            existing["value_type"],
            existing["status"],
            existing["verbatim"],
            existing["confidence"],
            existing["sequence"],
        ),
    )
    run(
        "UPDATE answers SET value_text=%s, value_type=%s, status=%s, verbatim=%s, confidence=%s,"
        " sequence=%s, updated_at=now() WHERE answer_id=%s",
        (
            value_text,
            value_type,
            answer.status.value,
            answer.verbatim,
            answer.confidence.value,
            answer.sequence,
            existing["answer_id"],
        ),
    )


def save_state(session_id: str, form: IntakeEngine) -> None:
    state = form.state.value
    run(
        "UPDATE intake_sessions SET state=%s, updated_at=now(),"
        " completed_at = CASE WHEN %s = 'complete' AND completed_at IS NULL THEN now() ELSE completed_at END"
        " WHERE session_id=%s",
        (state, state, session_id),
    )


def question_payload(form: IntakeEngine) -> dict:
    """The next question, described completely enough that the agent needs no form of its own."""
    item = form.next_item()
    answered, total = form.progress()
    if item is None:
        return {
            "done": True,
            "field_id": None,
            "ask": "",
            "progress": {"answered": answered, "askable": total},
            "missing_required": form.missing_required(),
        }
    field = item.field
    # The questions just over the horizon, with their ids. Without these the agent cannot record
    # anything it was not asked: a caller who says "we own it and recording is fine" has answered two
    # fields, and the model can only send an id it has been given. Measured: it recorded ownership,
    # then asked about the recording disclosure three times because it had no id for it.
    upcoming = []
    for other in form.plan():
        if len(upcoming) >= UPCOMING_FIELDS:
            break
        if other.field.id == field.id or other.field.id in form.answers:
            continue
        if form.applies(other.field) is not True:
            continue
        upcoming.append(
            {
                "field_id": other.field.id,
                "ask": other.prompt(),
                "answer_type": other.field.type.value,
                "choices": list(other.field.choices),
            }
        )
    return {
        "done": False,
        # Everything a quote needs is in. A real intake stops here and hands over rather than keeping
        # somebody on the phone for the optional remainder: 17 of the 92 fields are required, and a
        # measured call spent 51 turns on the plan in order and timed out before it ever transferred.
        "quotable": not form.missing_required(),
        "field_id": field.id,
        "upcoming": upcoming,
        "section": field.section,
        "ask": item.prompt(),
        "reprompts": list(field.reprompts),
        "answer_type": field.type.value,
        "choices": list(field.choices),
        "required_for_quote": field.required_for_quote,
        "fatal_if_refused": field.fatal_if_refused,
        "readback_required": field.type.value in READBACK_TYPES or field.id in READBACK_FIELDS,
        "progress": {"answered": answered, "askable": total},
        "already_settled": sorted(form.answers.keys()),
        "refused": form.refused(),
        "unknown": form.unknown(),
    }


# ---------------------------------------------------------------- models


class PhoneIn(BaseModel):
    phone: str = Field(pattern=r"^\+[1-9]\d{7,14}$")


class LeadIn(BaseModel):
    lead_id: str


class SessionIn(BaseModel):
    session_id: str


class StartCallIn(BaseModel):
    lead_id: str
    room_name: str = ""
    resume: bool = True


class AnswerIn(BaseModel):
    field_id: str
    text: str


class RecordAnswersIn(BaseModel):
    session_id: str
    answers: list[AnswerIn]


class FieldIn(BaseModel):
    session_id: str
    field_id: str
    said: str = ""


class ConsentIn(BaseModel):
    lead_id: str
    kind: str
    granted: bool
    verbatim: str = ""
    call_id: str | None = None


class SuppressIn(BaseModel):
    phone: str
    reason: str = ""
    verbatim: str = ""
    source: str = "caller_request"


class CallbackIn(BaseModel):
    lead_id: str
    window: str = "no_preference"
    notes: str = ""
    call_id: str | None = None


class TransferIn(BaseModel):
    call_id: str
    reason: str = ""
    to_number: str = ""


class EndCallIn(BaseModel):
    call_id: str
    disposition: str
    ended_reason: str = ""
    duration_seconds: int = 0
    recording_url: str = ""


# ---------------------------------------------------------------- health


@app.get("/health")
def health() -> dict:
    try:
        one("SELECT 1 AS ok")
        return {"ok": True, "questionnaire": INTAKE.name, "version": INTAKE.version}
    except Exception as exc:  # surfaced so the agent can say it is having trouble
        raise HTTPException(503, "db_unavailable") from exc


# ---------------------------------------------------------------- leads


@app.post("/lookup_lead_by_phone")
def lookup_lead_by_phone(body: PhoneIn) -> dict:
    lead = one("SELECT * FROM leads WHERE phone = %s", (body.phone,))
    if not lead:
        return {"lead_id": None, "suppressed": _suppressed(body.phone)}
    partner = (
        one("SELECT * FROM lender_partners WHERE partner_id = %s", (lead["partner_id"],))
        if lead["partner_id"]
        else None
    )
    return {
        "lead_id": lead["lead_id"],
        "full_name": lead["full_name"],
        "phone": lead["phone"],
        "property_address_hint": lead["property_address_hint"],
        "property_state": lead["property_state"],
        "partner_reference": lead["partner_reference"],
        "referral_label": partner["referral_label"] if partner else "your mortgage lender",
        "time_zone": lead["time_zone"],
        "status": lead["status"],
        "attempts": lead["attempts"],
        "suppressed": _suppressed(lead["phone"]),
    }


def _suppressed(phone: str) -> bool:
    return one("SELECT 1 AS hit FROM do_not_call WHERE phone = %s", (phone,)) is not None


@app.post("/is_suppressed")
def is_suppressed(body: PhoneIn) -> dict:
    return {"suppressed": _suppressed(body.phone)}


@app.post("/suppress_number")
def suppress_number(body: SuppressIn) -> dict:
    """Honour a removal request. Idempotent, because a caller may say it twice and mean it once."""
    if not _suppressed(body.phone):
        run(
            "INSERT INTO do_not_call (dnc_id, phone, reason, source, verbatim)"
            " VALUES (%s,%s,%s,%s,%s)",
            (new_id("dnc"), body.phone, body.reason, body.source, body.verbatim),
        )
    run("UPDATE leads SET status = 'suppressed' WHERE phone = %s", (body.phone,))
    audit("do_not_call", body.phone, "suppressed", {"reason": body.reason, "source": body.source})
    return {"suppressed": True, "phone": body.phone}


# ---------------------------------------------------------------- calls


@app.post("/start_call")
def start_call(body: StartCallIn) -> dict:
    lead = one("SELECT * FROM leads WHERE lead_id = %s", (body.lead_id,))
    if not lead:
        raise HTTPException(404, "lead_not_found")
    if _suppressed(lead["phone"]):
        raise HTTPException(409, "lead_suppressed")

    session = None
    if body.resume:
        session = one(
            "SELECT * FROM intake_sessions WHERE lead_id = %s AND state = 'in_progress'"
            " AND questionnaire = %s ORDER BY created_at DESC LIMIT 1",
            (body.lead_id, INTAKE.name),
        )
    resumed = session is not None
    if session is None:
        session_id = new_id("ses")
        run(
            "INSERT INTO intake_sessions (session_id, lead_id, questionnaire, questionnaire_version)"
            " VALUES (%s,%s,%s,%s)",
            (session_id, body.lead_id, INTAKE.name, INTAKE.version),
        )
    else:
        session_id = session["session_id"]

    attempt = int(lead["attempts"]) + 1
    call_id = new_id("cal")
    run("UPDATE leads SET attempts = %s WHERE lead_id = %s", (attempt, body.lead_id))
    run(
        "INSERT INTO call_attempts (call_id, lead_id, session_id, room_name, attempt_number, status)"
        " VALUES (%s,%s,%s,%s,%s,'in_progress')",
        (call_id, body.lead_id, session_id, body.room_name, attempt),
    )
    audit("call_attempt", call_id, "started", {"lead_id": body.lead_id, "attempt": attempt, "resumed": resumed})

    return {
        "call_id": call_id,
        "session_id": session_id,
        "resumed": resumed,
        "attempt_number": attempt,
        "next_question": serve_question(session_id),
    }


@app.post("/end_call")
def end_call(body: EndCallIn) -> dict:
    call = one("SELECT * FROM call_attempts WHERE call_id = %s", (body.call_id,))
    if not call:
        raise HTTPException(404, "call_not_found")
    run(
        "UPDATE call_attempts SET status='completed', disposition=%s, ended_reason=%s,"
        " duration_seconds=%s, recording_url=%s, ended_at=now() WHERE call_id=%s",
        (body.disposition, body.ended_reason, body.duration_seconds, body.recording_url, body.call_id),
    )
    lead_status = {
        "removed": "suppressed",
        "wrong_number": "suppressed",
        "transferred": "transferred",
        "completed": "completed",
    }.get(body.disposition, "retry")
    run("UPDATE leads SET status=%s WHERE lead_id=%s", (lead_status, call["lead_id"]))
    audit("call_attempt", body.call_id, "ended", {"disposition": body.disposition, "reason": body.ended_reason})
    return {"call_id": body.call_id, "disposition": body.disposition, "lead_status": lead_status}


# ---------------------------------------------------------------- the form


# Three asks, then it is recorded as not obtained and the call moves on. A caller who keeps talking
# about something else would otherwise be asked the same question for the rest of the call: measured,
# the language question was asked three turns running and would not have stopped.
MAX_ASKS_PER_FIELD = 3


def record_unknown(session_id: str, field_id: str, why: str) -> None:
    """Give up on one field, as unknown rather than blank, so the reason survives the call."""
    persist(
        session_id,
        Answer(
            field_id=field_id,
            value=None,
            status=AnswerStatus.UNKNOWN,
            verbatim=why,
            confidence=Confidence.LOW,
            sequence=0,
        ),
    )
    audit("session", session_id, "field_given_up", {"field_id": field_id, "why": why})


def serve_question(session_id: str) -> dict:
    """The next question, with a field nobody will answer given up on rather than repeated."""
    form = load_engine(session_id)
    attempts = (one(
        "SELECT ask_attempts FROM intake_sessions WHERE session_id = %s", (session_id,)
    ) or {}).get("ask_attempts") or {}
    while True:
        item = form.next_item()
        if item is None:
            break
        field_id = item.field.id
        seen = int(attempts.get(field_id, 0)) + 1
        attempts[field_id] = seen
        if seen <= MAX_ASKS_PER_FIELD:
            break
        record_unknown(session_id, field_id, f"not obtained after {MAX_ASKS_PER_FIELD} asks")
        form = load_engine(session_id)
    run(
        "UPDATE intake_sessions SET ask_attempts = %s, updated_at = now() WHERE session_id = %s",
        (Json(attempts), session_id),
    )
    return question_payload(form)


@app.post("/next_question")
def next_question(body: SessionIn) -> dict:
    _session_or_404(body.session_id)
    return serve_question(body.session_id)


def _session_or_404(session_id: str) -> dict:
    session = one("SELECT * FROM intake_sessions WHERE session_id = %s", (session_id,))
    if not session:
        raise HTTPException(404, "session_not_found")
    return session


@app.post("/record_answers")
def record_answers(body: RecordAnswersIn) -> dict:
    """Take everything heard in one turn, in the order it was said.

    Order matters only where one answer opens a branch another belongs to, and the order the caller
    said them in is the closest thing to their intent.
    """
    _session_or_404(body.session_id)
    form = load_engine(body.session_id)
    results = []
    for item in body.answers:
        # The value, not the object. `Answer` is mutable and `record` updates it in place, so holding
        # the row here would report the new value as the old one.
        existing = form.answers.get(item.field_id)
        before_value = existing.value if existing is not None else None
        had_answer = existing is not None
        result = form.record(item.field_id, item.text)
        payload: dict[str, Any] = {
            "field_id": item.field_id,
            "accepted": result.accepted,
            "problem": result.problem,
            "corrected": result.corrected,
            "fatal": result.fatal,
        }
        if result.accepted:
            answer = form.answers[result.field_id]
            persist(body.session_id, answer)
            payload["value"] = answer.value
            payload["confidence"] = answer.confidence.value
            field = form.item_for(result.field_id)
            if field is not None:
                needs = (
                    field.field.type.value in READBACK_TYPES
                    or field.field.id in READBACK_FIELDS
                )
                payload["readback_required"] = needs
            if had_answer and result.corrected:
                payload["previous_value"] = before_value
        results.append(payload)
    save_state(body.session_id, form)
    return {"results": results, "next_question": serve_question(body.session_id)}


@app.post("/refuse_answer")
def refuse_answer(body: FieldIn) -> dict:
    """A refusal is stored as a refusal with the words used, never left as an empty field."""
    _session_or_404(body.session_id)
    form = load_engine(body.session_id)
    result = form.refuse(body.field_id, body.said)
    if not result.accepted:
        raise HTTPException(400, result.problem or "field_not_on_form")
    persist(body.session_id, form.answers[body.field_id])
    save_state(body.session_id, form)
    return {
        "field_id": body.field_id,
        "status": "refused",
        "fatal": result.fatal,
        "next_question": serve_question(body.session_id),
    }


@app.post("/answer_unknown")
def answer_unknown(body: FieldIn) -> dict:
    _session_or_404(body.session_id)
    form = load_engine(body.session_id)
    result = form.mark_unknown(body.field_id, body.said)
    if not result.accepted:
        raise HTTPException(400, result.problem or "field_not_on_form")
    persist(body.session_id, form.answers[body.field_id])
    save_state(body.session_id, form)
    return {
        "field_id": body.field_id,
        "status": "unknown",
        "next_question": serve_question(body.session_id),
    }


# ---------------------------------------------------------------- consent and eligibility


@app.post("/record_consent")
def record_consent(body: ConsentIn) -> dict:
    if not one("SELECT 1 AS hit FROM leads WHERE lead_id = %s", (body.lead_id,)):
        raise HTTPException(404, "lead_not_found")
    if body.kind not in {"recording", "continue", "email", "transfer"}:
        raise HTTPException(400, "unknown_consent_kind")
    run(
        "INSERT INTO consent_events (consent_id, lead_id, call_id, kind, granted, verbatim)"
        " VALUES (%s,%s,%s,%s,%s,%s)",
        (new_id("con"), body.lead_id, body.call_id, body.kind, body.granted, body.verbatim),
    )
    audit("consent", body.lead_id, body.kind, {"granted": body.granted})
    return {"recorded": True, "kind": body.kind, "granted": body.granted}


@app.post("/check_eligibility")
def check_eligibility(body: SessionIn) -> dict:
    """The verdict, computed here from the rules. The agent is told what to say, not asked to decide."""
    session = _session_or_404(body.session_id)
    campaign = one(
        "SELECT c.reference_year FROM campaigns c JOIN leads l ON l.campaign_id = c.campaign_id"
        " WHERE l.lead_id = %s",
        (session["lead_id"],),
    )
    reference_year = int(campaign["reference_year"]) if campaign else 2026

    form = load_engine(body.session_id)
    result = evaluate(form.values(), reference_year=reference_year)
    answered, total = form.progress()
    spoken = result.spoken_reason()
    run(
        "INSERT INTO eligibility_decisions (decision_id, session_id, decision, hard_codes, soft_codes,"
        " indeterminate_codes, notes, spoken_reason, rules_reference_year)"
        " VALUES (%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s)",
        (
            new_id("dec"),
            body.session_id,
            result.decision.value,
            json.dumps([one_.code for one_ in result.hard]),
            json.dumps([one_.code for one_ in result.soft]),
            json.dumps([one_.code for one_ in result.indeterminate]),
            json.dumps([one_.internal_note for one_ in (*result.hard, *result.soft) if one_.internal_note]),
            spoken,
            reference_year,
        ),
    )
    audit(
        "eligibility",
        body.session_id,
        result.decision.value,
        {"hard": [one_.code for one_ in result.hard], "soft": [one_.code for one_ in result.soft]},
    )
    return {
        "decision": result.decision.value,
        "transferable": result.transferable,
        # The one sentence the agent is permitted to say about this. Empty when there is nothing to say.
        "spoken_reason": spoken,
        "hard": [one_.code for one_ in result.hard],
        "soft": [one_.code for one_ in result.soft],
        "indeterminate": [
            {"code": one_.code, "missing": list(one_.missing)} for one_ in result.indeterminate
        ],
        "agent_notes": [one_.internal_note for one_ in (*result.hard, *result.soft) if one_.internal_note],
        "progress": {"answered": answered, "askable": total},
        "missing_required": form.missing_required(),
    }


# ---------------------------------------------------------------- outcomes


@app.post("/request_transfer")
def request_transfer(body: TransferIn) -> dict:
    call = one("SELECT * FROM call_attempts WHERE call_id = %s", (body.call_id,))
    if not call:
        raise HTTPException(404, "call_not_found")
    collected = one(
        "SELECT count(*) AS total FROM answers WHERE session_id = %s", (call["session_id"],)
    )
    number = body.to_number or os.environ.get("LICENSED_AGENT_NUMBER", "")
    run(
        "INSERT INTO transfers (transfer_id, call_id, to_number, reason, answers_collected)"
        " VALUES (%s,%s,%s,%s,%s)",
        (new_id("trf"), body.call_id, number, body.reason, int(collected["total"]) if collected else 0),
    )
    audit("transfer", body.call_id, "requested", {"reason": body.reason})
    return {"requested": True, "to_number": number, "reason": body.reason}


@app.post("/schedule_callback")
def schedule_callback(body: CallbackIn) -> dict:
    if not one("SELECT 1 AS hit FROM leads WHERE lead_id = %s", (body.lead_id,)):
        raise HTTPException(404, "lead_not_found")
    run(
        "INSERT INTO callback_requests (callback_id, lead_id, call_id, window_label, notes)"
        " VALUES (%s,%s,%s,%s,%s)",
        (new_id("cbk"), body.lead_id, body.call_id, body.window, body.notes),
    )
    run("UPDATE leads SET status='retry' WHERE lead_id=%s", (body.lead_id,))
    audit("callback", body.lead_id, "scheduled", {"window": body.window})
    return {"scheduled": True, "window": body.window}


@app.post("/intake_result")
def intake_result(body: SessionIn) -> dict:
    """Everything a licensed agent needs to pick this up, including what is missing and why."""
    session = _session_or_404(body.session_id)
    form = load_engine(body.session_id)
    rows = many(
        "SELECT field_id, value_text, value_type, status, verbatim, confidence FROM answers"
        " WHERE session_id = %s ORDER BY sequence",
        (body.session_id,),
    )
    revisions = many(
        "SELECT a.field_id, r.value_text, r.status, r.verbatim FROM answer_revisions r"
        " JOIN answers a ON a.answer_id = r.answer_id WHERE a.session_id = %s ORDER BY r.sequence",
        (body.session_id,),
    )
    decision = one(
        "SELECT * FROM eligibility_decisions WHERE session_id = %s ORDER BY decided_at DESC LIMIT 1",
        (body.session_id,),
    )
    return {
        "session_id": body.session_id,
        "lead_id": session["lead_id"],
        "state": session["state"],
        "questionnaire_version": session["questionnaire_version"],
        "summary": form.summary(),
        "answers": [
            {
                "field_id": row["field_id"],
                "value": decode_value(row["value_text"], row["value_type"]),
                "status": row["status"],
                "verbatim": row["verbatim"],
                "confidence": row["confidence"],
            }
            for row in rows
        ],
        "revisions": [
            {
                "field_id": row["field_id"],
                "previous_value": row["value_text"],
                "previous_status": row["status"],
                "previous_verbatim": row["verbatim"],
            }
            for row in revisions
        ],
        "decision": {
            "decision": decision["decision"],
            "spoken_reason": decision["spoken_reason"],
            "hard": decision["hard_codes"],
            "soft": decision["soft_codes"],
            "notes": decision["notes"],
        }
        if decision
        else None,
    }


@app.post("/calling_window")
def calling_window(body: LeadIn) -> dict:
    """Whether this lead may be dialled right now, in the property's own local time."""
    lead = one("SELECT * FROM leads WHERE lead_id = %s", (body.lead_id,))
    if not lead:
        raise HTTPException(404, "lead_not_found")
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        zone = ZoneInfo(lead["time_zone"])
    except (ZoneInfoNotFoundError, ValueError):
        return {"allowed": False, "reason": f"unknown time zone {lead['time_zone']!r}"}
    moment = datetime.now(tz=zone)
    earliest = int(os.environ.get("CALLING_EARLIEST_HOUR", "9"))
    latest = int(os.environ.get("CALLING_LATEST_HOUR", "20"))
    if moment.weekday() == 6:
        return {"allowed": False, "reason": "we do not call on Sundays", "local_time": moment.isoformat()}
    if moment.weekday() == 5:
        earliest = int(os.environ.get("CALLING_SATURDAY_EARLIEST_HOUR", "10"))
        latest = int(os.environ.get("CALLING_SATURDAY_LATEST_HOUR", "17"))
    if moment.hour < earliest:
        return {"allowed": False, "reason": f"too early locally, opens at {earliest}", "local_time": moment.isoformat()}
    if moment.hour >= latest:
        return {"allowed": False, "reason": f"too late locally, closes at {latest}", "local_time": moment.isoformat()}
    return {"allowed": True, "reason": "", "local_time": moment.isoformat()}


@app.post("/leads_to_call")
def leads_to_call(body: LeadIn | None = None) -> dict:
    """The queue a campaign run works through, with suppressed numbers already removed."""
    rows = many(
        "SELECT l.lead_id, l.full_name, l.phone, l.time_zone, l.attempts, c.max_attempts"
        " FROM leads l JOIN campaigns c ON c.campaign_id = l.campaign_id"
        " WHERE c.active AND l.status IN ('pending','retry') AND l.attempts < c.max_attempts"
        " AND NOT EXISTS (SELECT 1 FROM do_not_call d WHERE d.phone = l.phone)"
        " ORDER BY l.created_at"
    )
    return {"leads": rows}
