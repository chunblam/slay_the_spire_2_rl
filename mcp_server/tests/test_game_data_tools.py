from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from sts2_mcp.server import _SCENE_FIELD_SETS, create_server, get_game_data_items_fields


class DummyClient:
    def __init__(self, screen: str = "MAIN_MENU") -> None:
        self._screen = screen

    def get_health(self) -> dict:
        return {"ok": True}

    def get_state(self) -> dict:
        return {"screen": self._screen, "available_actions": []}

    def get_available_actions(self) -> list[dict]:
        return []

    def wait_for_event(self, *, event_names=None, timeout=0.0) -> dict | None:
        return None

    def execute_action(self, *args, **kwargs) -> dict:
        return {"ok": True}


class GameDataToolsTests(unittest.TestCase):
    def test_get_game_data_item_supports_case_insensitive_lookup(self) -> None:
        client = DummyClient()
        server = create_server(client=client)
        tool = asyncio.run(server.get_tool("get_game_data_item"))
        abrasive = {"id": "ABRASIVE", "name": "Abrasive"}

        with patch("sts2_mcp.server._ensure_game_data_index", return_value={"ABRASIVE": abrasive}):
            result = tool.fn(collection="cards", item_id="abrasive")

        self.assertEqual(result, abrasive)

    def test_get_game_data_items_returns_batch_result(self) -> None:
        client = DummyClient()
        server = create_server(client=client)
        tool = asyncio.run(server.get_tool("get_game_data_items"))
        abrasive = {"id": "ABRASIVE", "name": "Abrasive"}
        jolt = {"id": "JOLT", "name": "Jolt"}

        with patch("sts2_mcp.server._ensure_game_data_index", return_value={"ABRASIVE": abrasive, "JOLT": jolt}):
            result = tool.fn(collection="cards", item_ids="abrasive, jolt, unknown")

        self.assertEqual(result["abrasive"], abrasive)
        self.assertEqual(result["jolt"], jolt)
        self.assertIsNone(result["unknown"])

    def test_get_relevant_game_data_uses_scene_fields_for_combat(self) -> None:
        client = DummyClient(screen="COMBAT_REWARD")
        server = create_server(client=client)
        tool = asyncio.run(server.get_tool("get_relevant_game_data"))
        expected = {"ABRASIVE": {"id": "ABRASIVE"}}
        expected_fields = ",".join(_SCENE_FIELD_SETS["combat"]["cards"])

        with patch(
            "sts2_mcp.server.get_game_data_items_fields",
            return_value=expected,
        ) as get_game_data_items_fields_mock:
            result = tool.fn(collection="cards", item_ids="ABRASIVE")

        self.assertEqual(result, expected)
        get_game_data_items_fields_mock.assert_called_once_with(
            collection="cards",
            item_ids="ABRASIVE",
            fields=expected_fields,
        )

    def test_get_relevant_game_data_falls_back_when_scene_has_no_field_set(self) -> None:
        client = DummyClient(screen="MAIN_MENU")
        server = create_server(client=client)
        tool = asyncio.run(server.get_tool("get_relevant_game_data"))
        event_item = {"id": "MYSTERY", "title": "Mystery Event"}

        with patch("sts2_mcp.server._ensure_game_data_index", return_value={"MYSTERY": event_item}):
            with patch("sts2_mcp.server.get_game_data_items_fields") as get_game_data_items_fields_mock:
                result = tool.fn(collection="events", item_ids="MYSTERY")

        self.assertEqual(result, {"MYSTERY": event_item})
        get_game_data_items_fields_mock.assert_not_called()

    def test_get_game_data_items_fields_filters_fields(self) -> None:
        with patch(
            "sts2_mcp.server._ensure_game_data_index",
            return_value={
                "ABRASIVE": {"id": "ABRASIVE", "name": "Abrasive", "cost": 2},
                "JOLT": {"id": "JOLT", "name": "Jolt", "cost": 1},
            },
        ):
            result = get_game_data_items_fields(
                collection="cards",
                item_ids="ABRASIVE, JOLT, UNKNOWN",
                fields="id,name",
            )

        self.assertEqual(result["ABRASIVE"], {"id": "ABRASIVE", "name": "Abrasive"})
        self.assertEqual(result["JOLT"], {"id": "JOLT", "name": "Jolt"})
        self.assertIsNone(result["UNKNOWN"])


if __name__ == "__main__":
    unittest.main()
