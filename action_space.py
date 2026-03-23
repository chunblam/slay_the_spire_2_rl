"""
src/env/action_space.py

将 RL 离散动作 ID 映射为 STS2MCP Raw API 的 POST JSON（根字段 action + 参数）。
规范见 docs/STS2MCP-Raw-API-中文调用文档.md
"""
from typing import Dict, List, Optional


def _first_alive_entity_id(monsters: List[dict]) -> Optional[str]:
    for m in monsters or []:
        if m.get("hp", 0) <= 0:
            continue
        eid = m.get("entity_id") or m.get("id")
        if eid is not None:
            return str(eid)
    return None


class STS2ActionSpace:
    """
    动作空间设计（按 screen_type 分组，与 sts2_env._adapt_raw_state 后的语义一致）

    战斗中 (COMBAT):
        0 ~ max_hand-1        → play_card（card_index + 可选 target entity_id）
        max_hand ~ +potions-1 → use_potion（slot + 可选 target）
        last                  → end_turn

    奖励选择 (CARD_REWARD):
        0=跳过，1..n → select_card_reward(card_index 0-based)

    地图导航 (MAP):
        0 ~ 6                 → choose_map_node（index 对应 next_options）

    休息 (REST):
        0~3                   → choose_rest_option（index）

    商店 (SHOP):
        0~7                   → shop_purchase；8 → proceed 离开

    事件 (EVENT):
        0~4                   → choose_event_option

    卡牌选择 (CARD_SELECT / GRID):
        0~9                   → select_card
    """

    def __init__(self, max_hand_size: int = 10, max_potions: int = 5):
        self.max_hand = max_hand_size
        self.max_potions = max_potions
        self.total_actions = max(16, 10)

    def decode(self, action_id: int, state: Dict) -> Dict:
        """
        返回可直接 POST 的 JSON 对象（含 \"action\" 字段）。
        """
        screen = state.get("screen_type", "NONE")

        if screen == "COMBAT":
            return self._decode_combat(action_id, state)
        if screen == "CARD_REWARD":
            return self._decode_card_reward(action_id, state)
        if screen == "MAP":
            return self._decode_map(action_id, state)
        if screen == "REST":
            return self._decode_rest(action_id, state)
        if screen in ("CARD_SELECT", "GRID"):
            return self._decode_card_select(action_id, state)
        if screen == "SHOP":
            return self._decode_shop(action_id, state)
        if screen == "EVENT":
            return self._decode_event(action_id, state)
        return {"action": "end_turn"}

    def _decode_combat(self, action_id: int, state: Dict) -> Dict:
        combat = state.get("combat", {})
        hand = combat.get("hand", [])
        potions = state.get("potions", [])
        end_turn_id = self.max_hand + self.max_potions
        monsters = combat.get("monsters", [])
        target = _first_alive_entity_id(monsters)

        if action_id < self.max_hand:
            if action_id < len(hand):
                body: Dict = {"action": "play_card", "card_index": action_id}
                if target is not None:
                    body["target"] = target
                return body
            return {"action": "end_turn"}

        if action_id < self.max_hand + self.max_potions:
            slot = action_id - self.max_hand
            if slot < len(potions):
                body = {"action": "use_potion", "slot": slot}
                if target is not None:
                    body["target"] = target
                return body
            return {"action": "end_turn"}

        return {"action": "end_turn"}

    def _decode_card_reward(self, action_id: int, state: Dict) -> Dict:
        """
        Raw API: skip_card_reward | select_card_reward（card_index 为 0-based，与 Mod 一致）。
        无需再包一层 payload；train._extract_agent_card_index 直接读这两个字段。
        """
        cards = state.get("card_reward", {}).get("cards", [])
        if not cards or action_id == 0 or action_id > len(cards):
            return {"action": "skip_card_reward"}
        return {"action": "select_card_reward", "card_index": action_id - 1}

    def _map_next_options(self, state: Dict) -> List:
        m = state.get("map") or {}
        opts = m.get("next_options")
        if opts is not None:
            return list(opts)
        nodes = m.get("next_nodes")
        return list(nodes) if nodes else []

    def _decode_map(self, action_id: int, state: Dict) -> Dict:
        nodes = self._map_next_options(state)
        if not nodes:
            return {"action": "choose_map_node", "index": 0}
        idx = min(action_id, len(nodes) - 1)
        return {"action": "choose_map_node", "index": idx}

    def _decode_rest(self, action_id: int, state: Dict) -> Dict:
        idx = min(max(action_id, 0), 3)
        return {"action": "choose_rest_option", "index": idx}

    def _decode_card_select(self, action_id: int, state: Dict) -> Dict:
        cards = (state.get("grid") or {}).get("cards", [])
        if not cards:
            cards = (state.get("card_select") or {}).get("cards", [])
        idx = min(action_id, len(cards) - 1) if cards else 0
        return {"action": "select_card", "index": idx}

    def _decode_shop(self, action_id: int, state: Dict) -> Dict:
        if action_id < 8:
            return {"action": "shop_purchase", "index": action_id}
        return {"action": "proceed"}

    def _decode_event(self, action_id: int, state: Dict) -> Dict:
        options = (state.get("event") or {}).get("options", [])
        idx = min(action_id, len(options) - 1) if options else 0
        return {"action": "choose_event_option", "index": idx}

    def get_valid_action_mask(self, state: Dict) -> List[bool]:
        """
        返回当前状态下每个动作是否有效的 mask
        """
        mask = [False] * self.total_actions
        screen = state.get("screen_type", "NONE")
        combat = state.get("combat", {})
        energy = combat.get("energy", 0)
        hand = combat.get("hand", [])

        if screen == "COMBAT":
            for i, card in enumerate(hand[: self.max_hand]):
                cost = card.get("cost", 0)
                cost_val = cost if isinstance(cost, int) else 0
                if cost_val <= energy or cost == "X":
                    mask[i] = True
            potions = state.get("potions", [])
            for i in range(min(len(potions), self.max_potions)):
                mask[self.max_hand + i] = True
            mask[self.max_hand + self.max_potions] = True

        elif screen in (
            "CARD_REWARD",
            "MAP",
            "REST",
            "EVENT",
            "SHOP",
            "CARD_SELECT",
            "GRID",
        ):
            for i in range(min(8, self.total_actions)):
                mask[i] = True

        return mask
