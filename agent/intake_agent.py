from __future__ import annotations

import logging
from typing import Any

from livekit.agents import Agent, function_tool
from livekit.agents.llm import ChatContext, ChatMessage, ToolError

from .objections import ObjectionExit, crossed
from .prompt import build_instructions, voicemail_message
from .speech import MISHEARD_LINES, readback
from .state import CONSENT_SECTION_FIELDS, GuardError, IntakeState
from .tools_client import ToolsAPIError, ToolsClient
from .voicemail import classify_opening, wrong_number

logger = logging.getLogger("alderway-voice-agent")

# Three tries at a line nobody can hear, then a callback. A fourth attempt annoys somebody who is
# already struggling to hear.
MAX_MISHEARD = 3
# A mailbox has to be caught on its greeting. Past this the classifier is not consulted again, because
# a person who says "leave a message" mid conversation is not a machine.
VOICEMAIL_DECIDING_TURNS = 2


def _tool_error(exc: Exception) -> ToolError:
    if isinstance(exc, (GuardError, ToolsAPIError)):
        return ToolError(str(exc))
    return ToolError("That could not be completed. Offer to call back rather than guessing.")


class IntakeAgent(Agent):
    """LiveKit agent whose tools enforce consent-before-collection and no-verdict-without-the-API.

    Nothing about the form lives here. Each tool returns the next question as the API computed it,
    which is what makes "it never re-asked an answered field" a property of the system rather than a
    hope about the model.
    """

    def __init__(self, state: IntakeState, client: ToolsClient, context: dict) -> None:
        self.state = state
        self.client = client
        self.context = context
        super().__init__(instructions=build_instructions(context))

    async def on_enter(self) -> None:
        from .prompt import opening_line

        await self.session.generate_reply(
            instructions=f'Say exactly this and nothing more: "{opening_line(self.context)}"',
            allow_interruptions=True,
        )

    async def on_user_turn_completed(self, turn_ctx: ChatContext, new_message: ChatMessage) -> None:
        """The checks that must run before the model is allowed to answer.

        Each of these overrides the conversation rather than joining it, so none is left to the
        model's judgement. A mailbox, a wrong number and a removal request all end the call, and a
        model that decides to be helpful instead is the failure mode this prevents.
        """
        said = str(new_message.text_content or "").strip()
        if not said:
            if self.state.note_misheard() >= MAX_MISHEARD:
                await self._say(MISHEARD_LINES[-1])
                await self._callback("no_preference", "caller could not be heard")
            else:
                await self._say(MISHEARD_LINES[min(self.state.misheard_streak - 1, len(MISHEARD_LINES) - 1)])
            return

        turns = len(self.state.asked)
        if turns <= VOICEMAIL_DECIDING_TURNS:
            verdict = classify_opening(said)
            if verdict.certain:
                logger.info("voicemail detected", extra={"matched": verdict.matched})
                await self._say(voicemail_message(self.context), allow_interruptions=False)
                self.state.disposition = "voicemail"
                self.state.ended_reason = f"machine answered ({verdict.matched})"
                await self._end_call()
                return

        if wrong_number(said):
            await self._say("I am sorry to have troubled you. I will take this number off the list.")
            await self._suppress(said, source="wrong_number", reason="wrong number")
            self.state.disposition = "wrong_number"
            self.state.ended_reason = "reached the wrong person"
            await self._end_call()
            return

        code = self.state.tracker.classify(said)
        if code is not None:
            await self._handle_objection(code, said)
            return

        boundary = crossed(said)
        if boundary is not None:
            # Said here rather than left to the model, because the wording of a refusal to give advice
            # is the part a regulator would read.
            logger.info("boundary crossed", extra={"kind": boundary.kind.value})
            await self._say(boundary.say)

    # ------------------------------------------------------------ helpers

    async def _say(self, text: str, *, allow_interruptions: bool = True) -> None:
        await self.session.say(text, allow_interruptions=allow_interruptions)

    async def _handle_objection(self, code: str, said: str) -> None:
        outcome = self.state.tracker.handle(code)
        logger.info("objection", extra={"code": code, "exit": outcome.exit.value})
        if outcome.exit is ObjectionExit.HONOUR_REMOVAL:
            if outcome.say:
                await self._say(outcome.say, allow_interruptions=False)
            await self._suppress(said, source="caller_request", reason="caller asked to be removed")
            await self._end_call()
            return
        if outcome.say:
            await self._say(outcome.say)
        if outcome.exit is ObjectionExit.SCHEDULE_CALLBACK:
            await self._callback("no_preference", said)
            return
        if outcome.exit is ObjectionExit.TRANSFER_HUMAN:
            await self._transfer(code)
            return
        if outcome.exit is ObjectionExit.END_POLITE:
            self.state.disposition = self.state.disposition or "declined"
            self.state.ended_reason = f"objection {code}"
            await self._end_call()
            return
        if self.state.tracker.over_budget():
            self.state.disposition = self.state.disposition or "declined"
            self.state.ended_reason = "too many objections"
            await self._end_call()

    async def _suppress(self, said: str, *, source: str, reason: str) -> None:
        await self.client.call(
            "suppress_number", phone=self.state.phone, reason=reason, verbatim=said, source=source
        )
        self.state.mark_suppressed(reason)

    async def _callback(self, window: str, notes: str) -> None:
        await self.client.call(
            "schedule_callback",
            lead_id=self.state.lead_id,
            window=window,
            notes=notes,
            call_id=self.state.call_id or None,
        )
        self.state.disposition = "callback"
        self.state.ended_reason = f"callback {window}"
        await self._end_call()

    async def _transfer(self, reason: str) -> None:
        await self.client.call("request_transfer", call_id=self.state.call_id, reason=reason)
        self.state.disposition = "transferred"
        self.state.ended_reason = reason
        await self._end_call()

    async def _end_call(self) -> None:
        if not self.state.call_id:
            return
        await self.client.call(
            "end_call",
            call_id=self.state.call_id,
            disposition=self.state.disposition or "dropped",
            ended_reason=self.state.ended_reason,
        )
        self.state.call_id = ""

    def describe_question(self, payload: dict[str, Any]) -> str:
        """Take the API's next question as the truth, and describe it for the model."""
        question = payload.get("next_question") or payload
        if question.get("done"):
            missing = question.get("missing_required") or []
            self.state.current_field = ""
            if missing:
                return (
                    "Every question that can be asked is settled, but these are still missing: "
                    + ", ".join(missing)
                    + ". Call check_eligibility."
                )
            return "The form is complete. Call check_eligibility."
        field_id = str(question.get("field_id") or "")
        self.state.note_asked(field_id)
        for one in question.get("refused") or []:
            self.state.settled.setdefault(one, "refused")
        for one in question.get("unknown") or []:
            self.state.settled.setdefault(one, "unknown")
        progress = question.get("progress") or {}
        lines = [
            f"Ask this now, in your own words and no longer: {question.get('ask')}",
            f"It records against {field_id}.",
            f"Answer type: {question.get('answer_type')}.",
        ]
        if question.get("choices"):
            lines.append(
                "Map their answer onto one of: "
                + ", ".join(question["choices"])
                + ". Do not read the list out unless they ask."
            )
        if question.get("readback_required"):
            lines.append("This one must be read back and agreed before you move on.")
        if question.get("fatal_if_refused"):
            lines.append(
                "Without this there is nothing to quote. If they refuse, say so kindly and call end_call."
            )
        if question.get("reprompts"):
            lines.append(f"If they do not follow, try: {question['reprompts'][0]}")
        upcoming = question.get("upcoming") or []
        if upcoming:
            # Ask one. Record any. A caller answers three things in a breath and each one needs an id
            # to land on, or it is lost and asked again.
            lines.append(
                "Ask ONLY the question above. But if they also answer any of these, record those in "
                "the same record_answers call:"
            )
            for other in upcoming:
                allowed = f" (one of: {', '.join(other['choices'])})" if other.get("choices") else ""
                lines.append(f"  {other['field_id']}: {other['ask']}{allowed}")
        lines.append(f"Progress: {progress.get('answered')} of {progress.get('askable')}.")
        return "\n".join(lines)

    # ------------------------------------------------------------ the form

    @function_tool()
    async def get_next_question(self) -> dict:
        """Ask the intake service what to ask next. Call this at the start and whenever you are unsure."""
        try:
            payload = await self.client.call("next_question", session_id=self.state.session_id)
            return {"instruction": self.describe_question(payload)}
        except Exception as exc:
            raise _tool_error(exc) from exc

    @function_tool()
    async def record_answers(self, field_ids: list[str], values: list[str]) -> dict:
        """Record everything the caller just said, including answers to questions you have not asked yet and corrections to earlier ones. field_ids and values must be the same length and line up."""
        if len(field_ids) != len(values):
            raise ToolError("field_ids and values must be the same length. Send them paired up.")

        pairs = list(zip(field_ids, values))
        # Consent first within the batch. A caller who says "recording is fine, go ahead, and it is
        # 2841 Wexford Lane" grants consent and volunteers detail in one breath, and recording the
        # consent has to unlock the rest of the same batch. Measured: validating the batch as a whole
        # discarded all of it, so the agent asked about the recording disclosure eight times in a row
        # and never advanced.
        pairs.sort(key=lambda pair: pair[0] not in CONSENT_SECTION_FIELDS)

        notes: list[str] = []
        allowed, held = self._partition(pairs)
        payload = await self._send_answers(allowed, notes) if allowed else {}
        # Consent may have just landed, so anything held back is worth one more look before it is
        # reported as refused by the guard.
        if held:
            second, blocked = self._partition([(one, two) for one, two, _ in held])
            for field_id, _, reason in held:
                if field_id not in [one for one, _ in second]:
                    notes.append(f"{field_id} not recorded: {reason}")
            if second:
                payload = await self._send_answers(second, notes)
        if not payload:
            payload = await self.client.call("next_question", session_id=self.state.session_id)
        return {"recorded": notes, "instruction": self.describe_question(payload)}

    def _partition(
        self, pairs: list[tuple[str, str]]
    ) -> tuple[list[tuple[str, str]], list[tuple[str, str, str]]]:
        """Split a batch into what may be recorded now and what may not, without losing either."""
        allowed: list[tuple[str, str]] = []
        held: list[tuple[str, str, str]] = []
        for field_id, text in pairs:
            try:
                self.state.ensure_askable(field_id)
                allowed.append((field_id, text))
            except GuardError as exc:
                held.append((field_id, text, str(exc)))
        return allowed, held

    async def _send_answers(self, pairs: list[tuple[str, str]], notes: list[str]) -> dict:
        try:
            payload = await self.client.call(
                "record_answers",
                session_id=self.state.session_id,
                answers=[{"field_id": one, "text": two} for one, two in pairs],
            )
        except Exception as exc:
            raise _tool_error(exc) from exc

        for result in payload.get("results", []):
            field_id = str(result.get("field_id"))
            if not result.get("accepted"):
                notes.append(f"{field_id} not recorded: {result.get('problem')}. Ask it again.")
                continue
            self.state.note_settled(field_id, "answered", result.get("value"))
            await self._maybe_record_consent(field_id, result.get("value"))
            notes.append(
                f"{field_id} {'corrected' if result.get('corrected') else 'recorded'} as "
                f"{result.get('value')!r}"
            )
            if result.get("readback_required"):
                question = payload.get("next_question") or {}
                spoken = readback(
                    str(result.get("answer_type") or self._type_of(field_id, question)),
                    result.get("value"),
                )
                if spoken:
                    notes.append(f"Say this and wait for them to agree: {spoken}")
            if result.get("confidence") == "low":
                notes.append(f"{field_id} was hedged, so confirm it once before moving on.")
            if result.get("fatal"):
                self.state.disposition = "declined_consent"
                self.state.ended_reason = f"{field_id} refused"
                notes.append("That cannot be skipped. Close the call politely and call end_call.")
        return payload

    def _type_of(self, field_id: str, question: dict) -> str:
        return str(question.get("answer_type") or "") if question.get("field_id") == field_id else ""

    async def _maybe_record_consent(self, field_id: str, value: Any) -> None:
        kind = {"recording_disclosure_ack": "recording", "consent_to_continue": "continue", "email_permission": "email"}.get(field_id)
        if kind is None:
            return
        try:
            await self.client.call(
                "record_consent",
                lead_id=self.state.lead_id,
                kind=kind,
                granted=bool(value),
                call_id=self.state.call_id or None,
            )
        except ToolsAPIError:
            # Consent is also on the answer row, so a failed event write is worth logging and not
            # worth ending a call over.
            logger.warning("consent event not written", extra={"field_id": field_id})

    @function_tool()
    async def refuse_answer(self, field_id: str, said: str = "") -> dict:
        """The caller will not give this answer. Record the refusal, with their words, and move on. Never ask it again."""
        if self.state.current_field and field_id != self.state.current_field:
            # Never redirected onto whatever is currently being asked. Measured: a refusal meant for
            # the premium landed on the recording disclosure, which is fatal if refused, and ended a
            # call the caller had not ended. Saying which field is on the table lets the model correct
            # itself and costs nothing.
            return {
                "instruction": (
                    f"Nothing recorded. You are on {self.state.current_field}, not {field_id}. "
                    f"If they refused {self.state.current_field}, call refuse_answer for that. "
                    "Otherwise ask the question you are on."
                )
            }
        try:
            payload = await self.client.call(
                "refuse_answer", session_id=self.state.session_id, field_id=field_id, said=said
            )
            self.state.note_settled(field_id, "refused")
            self.state.ensure_refusal_recorded(field_id)
            if payload.get("fatal"):
                self.state.disposition = "declined_consent"
                self.state.ended_reason = f"{field_id} refused"
                return {
                    "instruction": (
                        "Recorded. Without this there is nothing to quote. Say you understand, that "
                        "you cannot go further, thank them, and call end_call."
                    )
                }
            return {"instruction": self.describe_question(payload)}
        except Exception as exc:
            raise _tool_error(exc) from exc

    @function_tool()
    async def answer_unknown(self, field_id: str, said: str = "") -> dict:
        """The caller is willing but does not know. Record that and move on. This is normal, not a failure."""
        try:
            payload = await self.client.call(
                "answer_unknown", session_id=self.state.session_id, field_id=field_id, said=said
            )
            self.state.note_settled(field_id, "unknown")
            return {"instruction": self.describe_question(payload)}
        except Exception as exc:
            raise _tool_error(exc) from exc

    # ------------------------------------------------------------ outcome

    @function_tool()
    async def check_eligibility(self) -> dict:
        """Ask whether what has been collected can be quoted. You may not say anything about eligibility until this has answered."""
        try:
            verdict = await self.client.call("check_eligibility", session_id=self.state.session_id)
            self.state.set_verdict(verdict)
        except Exception as exc:
            raise _tool_error(exc) from exc

        if verdict.get("decision") == "disqualified":
            return {
                "decision": "disqualified",
                "instruction": (
                    "Say exactly this reason, once, and add nothing to it: "
                    f"\"{verdict.get('spoken_reason')}\" Then thank them and call end_call. "
                    "Do not offer a transfer, a price, or an alternative."
                ),
            }
        if verdict.get("decision") == "needs_review":
            return {
                "decision": "needs_review",
                "instruction": (
                    "A person has to look at this one. Say a colleague will review it and be in "
                    "touch, then offer to book a callback."
                ),
            }
        progress = verdict.get("progress") or {}
        if int(progress.get("answered") or 0) < 12:
            return {
                "decision": "eligible",
                "instruction": "Too little is collected for a licensed agent to work with. Carry on with the form.",
            }
        return {
            "decision": "eligible",
            "notes_for_agent": verdict.get("soft") or [],
            "instruction": (
                "This can be quoted. Offer to put them through to a licensed agent now, or to book a "
                "callback if they would rather. Do not mention a price."
            ),
        }

    @function_tool()
    async def transfer_to_licensed_agent(self, reason: str = "caller_request") -> dict:
        """Hand the call to a licensed human agent. Use this when they ask for a person, or ask something you are not allowed to answer."""
        try:
            if self.state.verdict is not None:
                # A disqualified intake has nothing to hand over, and offering a transfer after a
                # decline is how a caller ends up on hold to hear the same answer again.
                self.state.ensure_transferable()
            await self._transfer(reason)
            return {"instruction": "Tell them you are putting them through now, then stop talking."}
        except Exception as exc:
            raise _tool_error(exc) from exc

    @function_tool()
    async def schedule_callback(self, window: str = "no_preference", notes: str = "") -> dict:
        """Book a callback and end the call. Use when they are busy, driving, or ask to be called later."""
        try:
            await self._callback(window, notes)
            return {"instruction": "Confirm the time in one sentence, thank them, and stop."}
        except Exception as exc:
            raise _tool_error(exc) from exc

    @function_tool()
    async def honour_removal_request(self, said: str = "") -> dict:
        """The caller asked not to be contacted again. Call this immediately, before saying anything else, and never try to talk them out of it. This also ends the call, so do not call end_call after it."""
        try:
            await self._suppress(said, source="caller_request", reason="caller asked to be removed")
            await self._end_call()
            return {
                "instruction": (
                    "They are off the list. Say you have removed them and will not call again, "
                    "apologise once, and ask nothing further."
                )
            }
        except Exception as exc:
            raise _tool_error(exc) from exc

    @function_tool()
    async def end_call(self, reason: str = "") -> dict:
        """End the call after you have said one closing sentence."""
        try:
            if not self.state.disposition:
                self.state.disposition = "completed" if not self.state.outstanding() else "dropped"
                self.state.ended_reason = reason or "agent ended the call"
            await self._end_call()
            return {"instruction": "Call ending. Say nothing further."}
        except Exception as exc:
            raise _tool_error(exc) from exc
