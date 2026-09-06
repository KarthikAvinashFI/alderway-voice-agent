"""Provider wiring, and the TypedDict keys nothing else would catch."""

from __future__ import annotations

import pytest
from alderway_voice_agent.config import (
    build_deepgram_stt,
    google_llm_kwargs,
    google_llm_thinking,
    turn_handling_options,
)
from livekit.agents import TurnHandlingOptions
from livekit.agents.voice.turn import (
    EndpointingOptions,
    InterruptionOptions,
    PreemptiveGenerationOptions,
)


def test_every_turn_handling_key_is_one_the_library_declares():
    """A TypedDict accepts a misspelled key and then ignores it, so barge-in silently never turns on."""
    options = turn_handling_options()
    assert set(options) <= set(TurnHandlingOptions.__annotations__)


def test_every_nested_key_is_one_the_library_declares():
    options = turn_handling_options()
    assert set(options["endpointing"]) <= set(EndpointingOptions.__annotations__)
    assert set(options["interruption"]) <= set(InterruptionOptions.__annotations__)
    assert set(options["preemptive_generation"]) <= set(PreemptiveGenerationOptions.__annotations__)


def test_interruptions_are_on_by_default():
    """A caller must be able to talk over a question. This defaulting off would be invisible."""
    assert turn_handling_options()["interruption"]["enabled"] is True


def test_interruptions_can_be_turned_off_for_a_test_run(monkeypatch):
    monkeypatch.setenv("AGENT_ALLOW_INTERRUPTION", "0")
    assert turn_handling_options()["interruption"]["enabled"] is False


def test_a_single_word_does_not_interrupt():
    assert turn_handling_options()["interruption"]["min_words"] >= 2


def test_the_endpointing_delay_is_longer_than_the_library_default():
    """Callers pause to think on the year the roof was done, and 0.5 cuts them off."""
    endpointing = turn_handling_options()["endpointing"]
    assert endpointing["min_delay"] >= 0.6
    assert endpointing["max_delay"] >= 3.0


def test_the_endpointing_delay_is_configurable(monkeypatch):
    monkeypatch.setenv("AGENT_MIN_ENDPOINTING_DELAY", "0.9")
    assert turn_handling_options()["endpointing"]["min_delay"] == 0.9


def test_vertex_is_preferred_when_a_service_account_is_present(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "a-project")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/creds.json")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    kwargs = google_llm_kwargs()
    assert kwargs["vertexai"] is True
    assert kwargs["project"] == "a-project"
    assert "api_key" not in kwargs


def test_a_gemini_key_is_used_only_when_vertex_is_not_configured(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    kwargs = google_llm_kwargs()
    assert kwargs["vertexai"] is False
    assert kwargs["api_key"] == "not-a-real-key"


def test_no_google_credentials_at_all_is_an_error_rather_than_a_silent_default(monkeypatch):
    for name in ("GOOGLE_CLOUD_PROJECT", "GOOGLE_APPLICATION_CREDENTIALS", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValueError):
        google_llm_kwargs()


def test_a_level_taking_model_gets_a_level_and_a_budget_taking_one_gets_a_budget():
    """Sending the wrong one is rejected on every inference, and the agent simply never speaks."""
    assert google_llm_thinking("gemini-3-flash")["thinking_config"] == {"thinking_level": "low"}
    assert google_llm_thinking("gemini-2.5-flash")["thinking_config"] == {"thinking_budget": 0}


def test_an_unrecognised_model_is_left_at_the_provider_default():
    assert google_llm_thinking("some-future-model") == {}


def test_flux_and_nova_reach_different_deepgram_apis():
    assert type(build_deepgram_stt("k", "flux-general-en")).__name__ == "STTv2"
    assert type(build_deepgram_stt("k", "nova-3")).__name__ == "STT"
