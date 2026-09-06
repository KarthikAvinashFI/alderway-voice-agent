"""Provider wiring. Settings come from the environment, credentials never from a committed file."""

from __future__ import annotations

import os

from livekit.agents import TurnHandlingOptions
from livekit.plugins import deepgram


def build_deepgram_stt(api_key: str, model: str):
    """Flux uses Deepgram's v2 API; Nova models use the v1 API.

    Numerals rather than spelled words, because most of this form is numbers and a transcript that
    says "nineteen ninety eight" costs a parse that can fail.
    """
    if model.startswith("flux-"):
        return deepgram.STTv2(api_key=api_key, model=model)
    return deepgram.STT(api_key=api_key, model=model, numerals=True)


def google_llm_kwargs() -> dict:
    """Vertex where a service account is present, the direct Gemini API otherwise.

    Vertex first because it authenticates from an application credential file, so no key has to travel
    through configuration at all.
    """
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    credentials = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if project and credentials:
        return {
            "vertexai": True,
            "project": project,
            "location": os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
        }
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if api_key:
        return {"vertexai": False, "api_key": api_key}
    raise ValueError(
        "Set GOOGLE_APPLICATION_CREDENTIALS and GOOGLE_CLOUD_PROJECT for Vertex, "
        "or set GEMINI_API_KEY for Gemini."
    )


def google_llm_thinking(model: str) -> dict:
    """Which deliberation control this model accepts, which is a provider fact rather than a version.

    A token budget sent to a model that wants a level is rejected on every inference, and the symptom
    is an agent that never speaks with nothing in the transcript to say why.
    """
    if model.startswith("gemini-3"):
        return {"thinking_config": {"thinking_level": "low"}}
    if model.startswith(("gemini-1.5", "gemini-2.0", "gemini-2.5")):
        return {"thinking_config": {"thinking_budget": 0}}
    return {}


def turn_handling_options() -> TurnHandlingOptions:
    """How turns are ended and interrupted.

    A TypedDict, which means a misspelled key is accepted and then ignored, so the names here are
    checked against the library's own declaration in tests/test_config.py rather than trusted.

    The delays are longer than the defaults on purpose. An intake is long and callers pause to think,
    especially on the year the roof was done, and cutting them off is the commonest complaint about an
    agent like this.
    """
    return TurnHandlingOptions(
        turn_detection="stt",
        endpointing={
            "min_delay": float(os.environ.get("AGENT_MIN_ENDPOINTING_DELAY", "0.6")),
            "max_delay": float(os.environ.get("AGENT_MAX_ENDPOINTING_DELAY", "3.5")),
        },
        interruption={
            # A caller must be able to talk over a question. The field being asked lives in the tools
            # API rather than in the prompt, so an interruption cannot lose it.
            "enabled": os.environ.get("AGENT_ALLOW_INTERRUPTION", "1").lower()
            not in {"0", "false", "no"},
            "discard_audio_if_uninterruptible": True,
            # Two words rather than one, so a cough or an "mm" does not stop the agent mid question.
            "min_words": int(os.environ.get("AGENT_MIN_INTERRUPTION_WORDS", "2")),
        },
        preemptive_generation={
            "enabled": os.environ.get("AGENT_PREEMPTIVE_GENERATION", "1").lower()
            not in {"0", "false", "no"}
        },
    )
