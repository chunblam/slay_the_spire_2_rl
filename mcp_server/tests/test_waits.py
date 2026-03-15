from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from sts2_mcp.client import Sts2ApiError, Sts2Client
from sts2_mcp.server import create_server


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class DummyClient:
    def __init__(self, states: list[dict], event: dict | None = None) -> None:
        self._states = list(states)
        self._event = event
        self.wait_calls = 0

    def get_health(self) -> dict:
        return {"ok": True}

    def get_state(self) -> dict:
        if len(self._states) > 1:
            return self._states.pop(0)
        return self._states[0]

    def get_available_actions(self) -> list[dict]:
        return [{"name": "act"}]

    def wait_for_event(self, *, event_names=None, timeout=0.0) -> dict | None:
        self.wait_calls += 1
        return self._event


class WaitBehaviorTests(unittest.TestCase):
    def test_wait_for_event_uses_absolute_deadline_slices(self) -> None:
        client = Sts2Client(base_url="http://127.0.0.1:8080")
        clock = FakeClock()
        observed_timeouts: list[float] = []

        def fake_iter_events(*, read_timeout=None, include_comments=False):
            observed_timeouts.append(float(read_timeout))
            clock.now += float(read_timeout)
            raise Sts2ApiError(
                status_code=0,
                code="connection_error",
                message="timed out",
                retryable=True,
            )
            yield

        client.iter_events = fake_iter_events  # type: ignore[method-assign]

        with patch("sts2_mcp.client.time.monotonic", new=clock.monotonic):
            event = client.wait_for_event(timeout=2.4)

        self.assertIsNone(event)
        self.assertEqual([round(value, 2) for value in observed_timeouts], [1.0, 1.0, 0.4])

    def test_wait_until_actionable_returns_immediately_when_state_is_actionable(self) -> None:
        client = DummyClient(states=[{"available_actions": ["proceed"]}])
        server = create_server(client=client)
        tool = asyncio.run(server.get_tool("wait_until_actionable"))

        result = tool.fn(timeout_seconds=20.0)

        self.assertEqual(result["source"], "state")
        self.assertFalse(result["matched"])
        self.assertEqual(client.wait_calls, 0)

    def test_wait_until_actionable_falls_back_to_polling(self) -> None:
        clock = FakeClock()
        client = DummyClient(
            states=[
                {"available_actions": []},
                {"available_actions": []},
                {"available_actions": ["proceed"]},
            ]
        )
        server = create_server(client=client)
        tool = asyncio.run(server.get_tool("wait_until_actionable"))

        with patch("sts2_mcp.server.time.monotonic", new=clock.monotonic):
            with patch("sts2_mcp.server.time.sleep", new=clock.sleep):
                result = tool.fn(timeout_seconds=2.0)

        self.assertEqual(result["source"], "polling")
        self.assertEqual(result["state"]["available_actions"], ["proceed"])


if __name__ == "__main__":
    unittest.main()
