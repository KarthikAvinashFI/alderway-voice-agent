"""Placing the call. Neither reference agent dials out, so this is the new part.

Two steps, and the order is the point: the agent is dispatched into the room first, then the number is
dialled. Dialling first means that the moment somebody picks up there is nobody there, and the first two
seconds of an outbound call decide whether they stay on the line.

Run it directly to dial one lead:

    python agent/outbound.py --phone +16145550118
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import uuid

import httpx
from dotenv import load_dotenv
from livekit import api

load_dotenv(".env.local")
logger = logging.getLogger("alderway-outbound")

AGENT_NAME = os.environ.get("LIVEKIT_AGENT_NAME", "alderway-intake")


class DialRefused(RuntimeError):
    """Raised rather than half placing a call, so a misconfigured deployment fails loudly."""


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise DialRefused(f"{name} is not set. Fill it in .env.local before dialling.")
    return value


async def check_before_dialling(tools_url: str, phone: str) -> dict:
    """The two things that make a dial legal: the number is not suppressed, and it is not the wrong hour.

    Checked against the tools API rather than locally, because the suppression list and the property's
    timezone both live there and a second copy of either is a second thing to get out of step.
    """
    async with httpx.AsyncClient(base_url=tools_url.rstrip("/"), timeout=10) as client:
        lead = (await client.post("/lookup_lead_by_phone", json={"phone": phone})).json()
        if not lead.get("lead_id"):
            raise DialRefused(f"no lead on file for {phone}")
        if lead.get("suppressed"):
            raise DialRefused(f"{phone} is on the do not call list")
        window = (await client.post("/calling_window", json={"lead_id": lead["lead_id"]})).json()
        if not window.get("allowed") and os.environ.get("IGNORE_CALLING_WINDOW", "").lower() not in {
            "1",
            "true",
            "yes",
        }:
            raise DialRefused(f"outside the calling window: {window.get('reason')}")
        return lead


async def place_call(phone: str, *, tools_url: str | None = None, room_name: str | None = None) -> dict:
    tools_url = tools_url or os.environ.get("TOOLS_API_URL", "http://localhost:18092")
    lead = await check_before_dialling(tools_url, phone)
    room = room_name or f"alderway-{uuid.uuid4().hex[:10]}"

    client = api.LiveKitAPI(
        url=_required("LIVEKIT_URL"),
        api_key=_required("LIVEKIT_API_KEY"),
        api_secret=_required("LIVEKIT_API_SECRET"),
    )
    try:
        # The number travels as dispatch metadata, which is how the entrypoint knows whose intake to
        # resume rather than starting a fresh one on every attempt.
        await client.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                room=room,
                agent_name=AGENT_NAME,
                metadata=json.dumps({"lead_id": lead["lead_id"], "phone": phone}),
            )
        )
        participant = await client.sip.create_sip_participant(
            api.CreateSIPParticipantRequest(
                sip_trunk_id=_required("SIP_OUTBOUND_TRUNK_ID"),
                sip_call_to=phone,
                room_name=room,
                participant_identity=f"caller-{lead['lead_id']}",
                participant_name=lead.get("full_name") or "Homeowner",
                # Held open until the line is answered, so a no answer is a no answer here rather than
                # a call that looks connected and records silence.
                wait_until_answered=True,
                max_call_duration=int(os.environ.get("MAX_CALL_SECONDS", "900")),
            )
        )
        logger.info("dialled", extra={"room": room, "identity": participant.participant_identity})
        return {"room": room, "identity": participant.participant_identity, "answered": True}
    except api.TwirpError as exc:
        logger.warning("dial failed", extra={"room": room, "code": exc.code})
        return {"room": room, "identity": "", "answered": False, "error": f"{exc.code}: {exc.message}"}
    finally:
        await client.aclose()


async def run_campaign(limit: int, *, tools_url: str | None = None) -> list[dict]:
    """Work the queue the API hands back, one call at a time.

    Sequential on purpose. Parallel dialling into a small licensed agent pool produces callers on hold,
    which is the thing an intake agent exists to avoid.
    """
    tools_url = tools_url or os.environ.get("TOOLS_API_URL", "http://localhost:18092")
    async with httpx.AsyncClient(base_url=tools_url.rstrip("/"), timeout=10) as client:
        queue = (await client.post("/leads_to_call", json={"lead_id": ""})).json().get("leads", [])
    results = []
    for lead in queue[:limit]:
        try:
            results.append({"phone": lead["phone"], **await place_call(lead["phone"], tools_url=tools_url)})
        except DialRefused as exc:
            results.append({"phone": lead["phone"], "answered": False, "error": str(exc)})
    return results


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Dial one lead, or work the campaign queue.")
    parser.add_argument("--phone", help="E.164 number of a seeded lead")
    parser.add_argument("--campaign", type=int, metavar="N", help="dial the first N leads in the queue")
    parser.add_argument("--room", help="room name to use, generated when omitted")
    args = parser.parse_args()

    if args.campaign:
        for result in asyncio.run(run_campaign(args.campaign)):
            print(json.dumps(result))
        return
    if not args.phone:
        parser.error("give --phone or --campaign")
    print(json.dumps(asyncio.run(place_call(args.phone, room_name=args.room)), indent=2))


if __name__ == "__main__":
    main()
