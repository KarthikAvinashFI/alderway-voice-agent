from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path

from alderway_voice_agent.config import (
    build_deepgram_stt,
    google_llm_kwargs,
    google_llm_thinking,
    turn_handling_options,
)
from alderway_voice_agent.intake_agent import IntakeAgent
from alderway_voice_agent.speech import SILENCE_LINES
from alderway_voice_agent.state import IntakeState
from alderway_voice_agent.tools_client import ToolsClient
from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import AgentServer, AgentSession, JobContext, cli, room_io
from livekit.plugins import deepgram, google, silero

load_dotenv(".env.local")
logger = logging.getLogger("alderway-voice-agent")

# Two prompts at a silent line, then the call is closed and left resumable. Somebody who has put the
# phone down deserves that rather than a third attempt.
MAX_SILENCE_PROMPTS = 2


async def resolve_lead(client: ToolsClient, phone: str) -> dict:
    """Who we are calling, from our own records. What the lender sent is never treated as verified."""
    lead = await client.call("lookup_lead_by_phone", phone=phone)
    if not lead.get("lead_id"):
        raise RuntimeError(f"no lead on file for {phone}")
    if lead.get("suppressed"):
        raise RuntimeError(f"lead {lead['lead_id']} is on the do not call list")
    return lead


server = AgentServer()


def build_audio_input_options(participant_identity: str | None) -> room_io.RoomOptions:
    options: dict = {"audio_input": room_io.AudioInputOptions()}
    if participant_identity:
        options["participant_identity"] = participant_identity
    return room_io.RoomOptions(**options)


def write_transcript(session_id: str, turns: list[dict], summary: dict) -> Path | None:
    """Keep the conversation on disk, because a claim about how a call went needs the call.

    Off by default. A transcript is personal information and a repository is the wrong place for it
    unless somebody has asked for one.
    """
    destination = os.environ.get("TRANSCRIPT_DIR", "").strip()
    if not destination:
        return None
    directory = Path(destination)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{session_id}.json"
    path.write_text(
        json.dumps({"session_id": session_id, "summary": summary, "turns": turns}, indent=2, default=str),
        encoding="utf-8",
    )
    readable = directory / f"{session_id}.txt"
    readable.write_text(
        "\n".join(f"[{one['at']:>7.2f}s] {one['role']:>9}: {one['text']}" for one in turns) + "\n",
        encoding="utf-8",
    )
    return path


@server.rtc_session(agent_name=os.environ.get("LIVEKIT_AGENT_NAME", "alderway-intake"))
async def entrypoint(ctx: JobContext) -> None:
    metadata: dict = {}
    if ctx.job and ctx.job.metadata:
        try:
            metadata = json.loads(ctx.job.metadata)
        except (TypeError, json.JSONDecodeError):
            logger.warning("job metadata was not readable json")

    await ctx.connect()

    # An outbound call carries the number in its dispatch metadata. Console and dev runs have none, so
    # they fall back to a seeded lead, which keeps every write on the same path as a real call.
    phone = str(metadata.get("phone") or "").strip() or os.environ.get(
        "DEMO_LEAD_PHONE", "+16145550118"
    )

    participant = None
    if metadata.get("phone"):
        # Wait for the dialled party rather than starting into an empty room, because the first two
        # seconds of an outbound call are the ones that decide whether they stay on the line.
        participant = await ctx.wait_for_participant()
        logger.info(
            "caller joined",
            extra={
                "identity": participant.identity,
                "is_sip": participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP,
            },
        )

    session_key = ctx.room.name or phone
    client = ToolsClient(
        os.environ.get("TOOLS_API_URL", "http://localhost:18092"),
        session_id=session_key,
        timeout=float(os.environ.get("TOOLS_TIMEOUT_SECONDS", "5")),
    )
    lead = await resolve_lead(client, phone)

    # Whether to place a call is decided before dialling, in `outbound.py`. By the time this runs
    # somebody is already connected, so an out-of-window call is recorded and answered rather than
    # hung up on: refusing to speak to a person on the line is worse than speaking to them, and
    # raising here crashed the job instead.
    window = await client.call("calling_window", lead_id=lead["lead_id"])
    if not window.get("allowed"):
        logger.warning("outside the calling window: %s", window.get("reason"))

    started = await client.call("start_call", lead_id=lead["lead_id"], room_name=ctx.room.name or "")
    # Setup is not part of the conversation. Trace from the first conversational action onward.
    client.enable_trace()

    state = IntakeState(
        lead_id=lead["lead_id"],
        phone=lead["phone"],
        session_id=started["session_id"],
        call_id=started["call_id"],
        lead_name=lead.get("full_name") or "",
    )
    context = {
        "agent_name": os.environ.get("AGENT_DISPLAY_NAME", "Avery"),
        "company": os.environ.get("COMPANY_NAME", "Alderway Insurance Services"),
        "referral": lead.get("referral_label") or "your mortgage lender",
        "lead_name": lead.get("full_name") or "",
        "phone": lead.get("phone") or "",
        "partner_reference": lead.get("partner_reference") or "",
        "property_hint": lead.get("property_address_hint") or "",
    }
    if started.get("resumed"):
        logger.info("resuming a partial intake", extra={"session_id": state.session_id})

    is_sip = bool(participant and participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP)
    deepgram_key = os.environ["DEEPGRAM_API_KEY"]
    stt_model = os.environ.get(
        "AGENT_STT_MODEL_PHONE" if is_sip else "AGENT_STT_MODEL",
        "nova-2-phonecall" if is_sip else "nova-3",
    )
    llm_model = os.environ.get("AGENT_LLM_MODEL", "gemini-2.5-flash")

    session = AgentSession(
        stt=build_deepgram_stt(deepgram_key, stt_model),
        llm=google.LLM(
            model=llm_model,
            temperature=float(os.environ.get("AGENT_LLM_TEMPERATURE", "0.3")),
            **google_llm_kwargs(),
            **google_llm_thinking(llm_model),
        ),
        tts=deepgram.TTS(
            api_key=deepgram_key,
            model=os.environ.get("AGENT_TTS_MODEL", "aura-2-andromeda-en"),
        ),
        turn_handling=turn_handling_options(),
        max_tool_steps=int(os.environ.get("AGENT_MAX_TOOL_STEPS", "12")),
        user_away_timeout=float(os.environ.get("AGENT_SILENCE_SECONDS", "9")),
        vad=silero.VAD.load(),
    )

    turns: list[dict] = []
    opened = time.monotonic()

    @session.on("conversation_item_added")
    def _on_item(event) -> None:
        item = getattr(event, "item", None)
        role = str(getattr(item, "role", "") or "")
        text = str(getattr(item, "text_content", "") or "")
        if role and text:
            turns.append({"role": role, "text": text, "at": round(time.monotonic() - opened, 2)})

    @session.on("user_state_changed")
    def _on_user_state(event) -> None:
        if str(getattr(event, "new_state", "")) != "away":
            return
        asyncio.create_task(_prompt_silence())

    async def _prompt_silence() -> None:
        if state.note_silence() > MAX_SILENCE_PROMPTS:
            state.disposition = state.disposition or "no_answer"
            state.ended_reason = "caller went quiet"
            return
        line = SILENCE_LINES[min(state.silence_prompts - 1, len(SILENCE_LINES) - 1)]
        await session.say(line)

    @session.on("error")
    def _on_error(event) -> None:
        logger.warning("session error", extra={"error": str(getattr(event, "error", ""))})

    agent = IntakeAgent(state, client, context)
    await session.start(
        agent=agent,
        room=ctx.room,
        room_options=build_audio_input_options(participant.identity if participant else None),
    )
    # Hand the model the first question straight away, so its opening turn is followed by the right
    # one rather than by whatever it remembers a form looking like.
    first = await client.call("next_question", session_id=state.session_id)
    await agent.update_instructions(
        agent.instructions + "\n\nCurrent task:\n" + agent.describe_question(first)
    )

    async def on_shutdown() -> None:
        try:
            result = await client.call("intake_result", session_id=state.session_id)
        except Exception:
            logger.warning("intake result unavailable at shutdown")
            result = {}
        if state.call_id:
            try:
                await client.call(
                    "end_call",
                    call_id=state.call_id,
                    disposition=state.disposition or "dropped",
                    ended_reason=state.ended_reason or "session ended",
                    duration_seconds=int(time.monotonic() - opened),
                )
            except Exception:
                logger.warning("call could not be closed cleanly")
        path = write_transcript(state.session_id, turns, result.get("summary") or {})
        logger.info(
            "call finished",
            extra={
                "session_id": state.session_id,
                "disposition": state.disposition or "dropped",
                "transcript": str(path) if path else "not written",
            },
        )

    ctx.add_shutdown_callback(on_shutdown)


if __name__ == "__main__":
    cli.run_app(server)
