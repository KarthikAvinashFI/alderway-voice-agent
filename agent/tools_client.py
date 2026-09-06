from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx


class ToolsAPIError(RuntimeError):
    pass


class ToolsClient:
    """HTTP client for the tools API, tracing every call it makes.

    The trace is what makes a claim about a call checkable afterwards: every answer the agent recorded
    and every verdict it spoke has a line here, so "it never re-asked an answered field" and "it did
    not invent that reason" are read off the evidence rather than argued about.
    """

    def __init__(
        self,
        base_url: str,
        *,
        session_id: str,
        timeout: float = 5.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.session_id = session_id
        self.timeout = timeout
        self._client = client
        self._trace_path: Path | None = None

    def enable_trace(self, destination: str | None = None) -> None:
        value = destination or os.environ.get("TOOL_TRACE", "")
        if value.strip():
            self._trace_path = Path(value)
            self._trace_path.parent.mkdir(parents=True, exist_ok=True)

    def record_local(self, name: str, arguments: dict[str, Any], output: dict[str, Any]) -> None:
        """Record a state-only tool that does not cross the HTTP boundary."""
        self._record(name, arguments, output, is_error=False)

    def _record(
        self,
        endpoint: str,
        payload: dict[str, Any],
        output: dict[str, Any] | str,
        *,
        is_error: bool,
    ) -> None:
        if self._trace_path is None:
            return
        try:
            with self._trace_path.open("a", encoding="utf-8") as trace:
                trace.write(
                    json.dumps(
                        {
                            "name": endpoint,
                            "arguments": payload,
                            "output": output,
                            "is_error": is_error,
                        },
                        default=str,
                    )
                    + "\n"
                )
        except OSError:
            # Evidence is best effort and must never change agent behaviour.
            return

    async def call(self, endpoint: str, **payload: Any) -> dict[str, Any]:
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(self.timeout),
            trust_env=False,
        )
        try:
            response = await client.post(
                f"/{endpoint}",
                json=payload,
                headers={"x-session-id": self.session_id},
            )
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict):
                raise ToolsAPIError("The local tools service returned an invalid response.")
            self._record(endpoint, payload, result, is_error=False)
            return result
        except (httpx.HTTPError, ValueError) as exc:
            detail = ""
            if isinstance(exc, httpx.HTTPStatusError) and 400 <= exc.response.status_code < 500:
                # The service is fine and the request was wrong, so say which. Hiding a 400 behind
                # "temporarily unavailable" leaves the model repeating the same mistaken call.
                try:
                    detail = str(exc.response.json().get("detail") or "")
                except ValueError:
                    detail = exc.response.text[:200]
            message = (
                f"{endpoint.replace('_', ' ')} was refused: {detail}"
                if detail
                else f"The {endpoint.replace('_', ' ')} service is temporarily unavailable."
            )
            self._record(endpoint, payload, message, is_error=True)
            raise ToolsAPIError(message) from exc
        finally:
            if owns_client:
                await client.aclose()
