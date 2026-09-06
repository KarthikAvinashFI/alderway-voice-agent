"""The HTTP client: what it sends, what it traces, and how a failure reaches the caller."""

from __future__ import annotations

import json

import httpx
import pytest
from alderway_voice_agent.tools_client import ToolsAPIError, ToolsClient


def _client(handler, **kwargs) -> ToolsClient:
    transport = httpx.MockTransport(handler)
    return ToolsClient(
        "http://tools",
        session_id="ses_test",
        client=httpx.AsyncClient(transport=transport, base_url="http://tools"),
        **kwargs,
    )


async def test_a_call_posts_to_the_endpoint_named():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        seen["session"] = request.headers.get("x-session-id")
        return httpx.Response(200, json={"ok": True})

    result = await _client(handler).call("next_question", session_id="ses_1")
    assert result == {"ok": True}
    assert seen["url"] == "http://tools/next_question"
    assert seen["body"] == {"session_id": "ses_1"}


async def test_the_session_id_travels_on_every_call():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-session-id"] == "ses_test"
        return httpx.Response(200, json={"ok": True})

    await _client(handler).call("health")


async def test_a_server_error_becomes_something_the_agent_can_say():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "boom"})

    with pytest.raises(ToolsAPIError) as caught:
        await _client(handler).call("check_eligibility", session_id="ses_1")
    assert "check eligibility service is temporarily unavailable" in str(caught.value)


async def test_a_transport_failure_becomes_the_same_kind_of_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(ToolsAPIError):
        await _client(handler).call("record_answers", session_id="ses_1")


async def test_a_response_that_is_not_an_object_is_refused():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["not", "a", "dict"])

    with pytest.raises(ToolsAPIError):
        await _client(handler).call("next_question")


async def test_a_successful_call_is_traced(tmp_path):
    trace = tmp_path / "trace.jsonl"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"decision": "eligible"})

    client = _client(handler)
    client.enable_trace(str(trace))
    await client.call("check_eligibility", session_id="ses_1")

    lines = [json.loads(one) for one in trace.read_text().splitlines()]
    assert len(lines) == 1
    assert lines[0]["name"] == "check_eligibility"
    assert lines[0]["arguments"] == {"session_id": "ses_1"}
    assert lines[0]["output"] == {"decision": "eligible"}
    assert lines[0]["is_error"] is False


async def test_a_failed_call_is_traced_as_an_error(tmp_path):
    trace = tmp_path / "trace.jsonl"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    client = _client(handler)
    client.enable_trace(str(trace))
    with pytest.raises(ToolsAPIError):
        await client.call("next_question")

    line = json.loads(trace.read_text().splitlines()[0])
    assert line["is_error"] is True


async def test_nothing_is_traced_until_tracing_is_enabled(tmp_path):
    trace = tmp_path / "trace.jsonl"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    await _client(handler).call("health")
    assert trace.exists() is False


async def test_a_local_tool_can_be_traced_alongside_the_http_ones(tmp_path):
    trace = tmp_path / "trace.jsonl"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    client = _client(handler)
    client.enable_trace(str(trace))
    client.record_local("say_readback", {"field_id": "quote_email"}, {"said": True})
    line = json.loads(trace.read_text().splitlines()[0])
    assert line["name"] == "say_readback"


async def test_an_unwritable_trace_destination_does_not_break_the_call(tmp_path):
    """Evidence is best effort. A full disk must never end somebody's phone call."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    client = _client(handler)
    client.enable_trace(str(tmp_path))  # a directory, so writing to it fails
    assert await client.call("health") == {"ok": True}
