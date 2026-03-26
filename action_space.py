from typing import Dict, List, Optional, Set


def _get_legal_actions(state: Dict) -> List[str]:
    legal = state.get("available_actions") or []
    return [str(a) for a in legal]


def _can_act_now(state: Dict) -> bool:
    return bool(state.get("can_act", True))


def _first_alive_target(monsters: List[dict]) -> Optional[str]:
    """
    Raw API: enemies 列表只包含存活目标；target 应使用 entity_id。
    兼容 env normalize 后的字段：id 可能来自 entity_id。
    """
    for m in monsters or []:
        if not isinstance(m, dict):
            continue
        # Prefer raw API key if present; otherwise fall back to normalized id.
        target = m.get("entity_id") or m.get("id")
        if target:
            return str(target)
    return None


class STS2ActionSpace:
    def __init__(
        self,
        max_hand_size: int = 10,
        max_potions: int = 5,
    ):
        self.max_hand = max_hand_size
        self.max_potions = max_potions
        self.total_actions = max(16, 10)

    def decode(self, action_id: int, state: Dict) -> Dict:
        screen = state.get("screen_type", "NONE")
        legal = _get_legal_actions(state)
        if not _can_act_now(state):
            # Env should gate posting when can_act=False; keep decode conservative.
            return self._fallback_from_legal_actions(state)

        if screen == "COMBAT":
            candidate = self._decode_combat(action_id, state)
        elif screen == "CARD_REWARD":
            candidate = self._decode_card_reward(action_id, state)
        elif screen == "REWARD":
            candidate = self._decode_reward(action_id, state)
        elif screen == "MAP":
            candidate = self._decode_map(action_id, state)
        elif screen == "REST":
            candidate = self._decode_rest(action_id, state)
        elif screen == "CARD_SELECT":
            candidate = self._decode_card_select(action_id, state)
        elif screen == "SHOP":
            candidate = self._decode_shop(action_id, state)
        elif screen == "EVENT":
            candidate = self._decode_event(action_id, state)
        elif screen == "CHOOSE_CARD_BUNDLE":
            candidate = self._decode_choose_card_bundle(action_id, state)
        elif screen == "CHEST":
            candidate = self._decode_chest(action_id, state)
        else:
            candidate = self._fallback_from_legal_actions(state)
        return self._ensure_legal(candidate, state)

    def _ensure_legal(self, candidate: Dict, state: Dict) -> Dict:
        legal = _get_legal_actions(state)
        action_name = str(candidate.get("action", ""))
        if not legal or not action_name:
            return candidate
        if action_name in legal:
            return candidate
        return self._fallback_from_legal_actions(state)

    @staticmethod
    def _fallback_from_legal_actions(state: Dict) -> Dict:
        legal = _get_legal_actions(state)
        if not legal:
            return {"action": "proceed"}

        # Menu bootstrap
        if "open_character_select" in legal:
            return {"action": "open_character_select"}
        if "select_character" in legal:
            return {"action": "select_character", "index": 0}
        if "embark" in legal:
            return {"action": "embark"}
        if "return_to_main_menu" in legal:
            return {"action": "return_to_main_menu"}

        # Core in-run actions
        if "end_turn" in legal:
            return {"action": "end_turn"}
        if "play_card" in legal:
            return {"action": "play_card", "card_index": 0}
        if "use_potion" in legal:
            return {"action": "use_potion", "slot": 0}

        if "select_card_reward" in legal:
            return {"action": "select_card_reward", "card_index": 0}
        if "skip_card_reward" in legal:
            return {"action": "skip_card_reward"}

        if "claim_reward" in legal:
            return {"action": "claim_reward", "index": 0}
        if "choose_map_node" in legal:
            return {"action": "choose_map_node", "index": 0}
        if "choose_event_option" in legal:
            return {"action": "choose_event_option", "index": 0}
        if "advance_dialogue" in legal:
            return {"action": "advance_dialogue"}
        if "choose_rest_option" in legal:
            return {"action": "choose_rest_option", "index": 0}

        if "shop_purchase" in legal:
            return {"action": "shop_purchase", "index": 0}

        if "combat_select_card" in legal:
            return {"action": "combat_select_card", "card_index": 0}
        if "combat_confirm_selection" in legal:
            return {"action": "combat_confirm_selection"}
        if "select_card" in legal:
            return {"action": "select_card", "index": 0}
        if "confirm_selection" in legal:
            return {"action": "confirm_selection"}
        if "cancel_selection" in legal:
            return {"action": "cancel_selection"}

        if "claim_treasure_relic" in legal:
            return {"action": "claim_treasure_relic", "index": 0}
        if "select_relic" in legal:
            return {"action": "select_relic", "index": 0}
        if "skip_relic_selection" in legal:
            return {"action": "skip_relic_selection"}

        if "choose_bundle" in legal:
            return {"action": "choose_bundle", "index": 0}

        if "proceed" in legal:
            return {"action": "proceed"}

        return {"action": legal[0]}

    def _decode_choose_card_bundle(self, action_id: int, state: Dict) -> Dict:
        legal = _get_legal_actions(state)
        card_bundle = state.get("card_bundle") or {}

        if "choose_bundle" in legal:
            controls = card_bundle.get("ui_controls") or []
            picks: List[dict] = []
            if isinstance(controls, list):
                for c in controls:
                    if isinstance(c, dict) and c.get("role") == "choose_bundle":
                        picks.append(c)
            pick_count = len(picks)
            if pick_count <= 1:
                idx = 0
            else:
                idx = int(action_id % 2)
                if idx >= pick_count:
                    idx = 0
            return {"action": "choose_bundle", "index": idx}

        if "proceed" in legal:
            return {"action": "proceed"}

        return self._fallback_from_legal_actions(state)

    def _decode_combat(self, action_id: int, state: Dict) -> Dict:
        combat = state.get("combat", {})
        hand = combat.get("hand", [])
        potions = state.get("potions", [])
        monsters = combat.get("monsters", [])
        target = _first_alive_target(monsters)

        if action_id < self.max_hand:
            if action_id < len(hand):
                body: Dict = {"action": "play_card", "card_index": action_id}
                # Raw API: needs target only for AnyEnemy-like cards; if requires_target is absent,
                # we only attach when present to avoid over-specifying.
                if target is not None and bool(hand[action_id].get("requires_target", False)):
                    body["target"] = target
                return body
            return {"action": "end_turn"}

        if action_id < self.max_hand + self.max_potions:
            slot = action_id - self.max_hand
            if slot < len(potions):
                body = {"action": "use_potion", "slot": slot}
                # Potions may or may not require a target; if we have a valid enemy, sending target
                # is typically accepted by the Raw API (and required for AnyEnemy potions).
                if target is not None:
                    body["target"] = target
                return body
            return {"action": "end_turn"}

        return {"action": "end_turn"}

    def _decode_card_reward(self, action_id: int, state: Dict) -> Dict:
        cards = state.get("card_reward", {}).get("cards", [])
        if not cards or action_id == 0 or action_id > len(cards):
            return {"action": "skip_card_reward"}
        return {"action": "select_card_reward", "card_index": action_id - 1}

    def _decode_reward(self, action_id: int, state: Dict) -> Dict:
        reward = state.get("reward") or {}
        rewards = reward.get("rewards") or []
        legal = _get_legal_actions(state)
        if rewards and "claim_reward" in legal:
            idx = min(max(action_id, 0), len(rewards) - 1)
            return {"action": "claim_reward", "index": idx}
        if "proceed" in legal:
            return {"action": "proceed"}
        return self._fallback_from_legal_actions(state)

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
            return self._fallback_from_legal_actions(state)
        idx = min(action_id, len(nodes) - 1)
        return {"action": "choose_map_node", "index": idx}

    def _decode_rest(self, action_id: int, state: Dict) -> Dict:
        """
        Raw API: rest_site.options 来自状态；不要写死“强制锻造/强制选牌升级”的流程。
        这里只负责在可用选项范围内选择 index。
        """
        rest = state.get("rest") or {}
        options = rest.get("options") or []
        idx = min(max(int(action_id), 0), max(len(options) - 1, 0)) if isinstance(options, list) else max(int(action_id), 0)
        return {"action": "choose_rest_option", "index": idx}

    def _decode_card_select(self, action_id: int, state: Dict) -> Dict:
        legal = _get_legal_actions(state)

        cards = (state.get("selection") or {}).get("cards", [])
        idx = min(action_id, len(cards) - 1) if cards else 0

        if "combat_select_card" in legal:
            return {"action": "combat_select_card", "card_index": idx}
        if "select_card" in legal:
            return {"action": "select_card", "index": idx}
        if "confirm_selection" in legal:
            return {"action": "confirm_selection"}
        if "combat_confirm_selection" in legal:
            return {"action": "combat_confirm_selection"}

        return self._fallback_from_legal_actions(state)

    def _decode_shop(self, action_id: int, state: Dict) -> Dict:
        shop = state.get("shop") or {}
        legal = _get_legal_actions(state)
        items = list(shop.get("items") or [])
        if "shop_purchase" in legal and items:
            buyable = []
            for i, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                affordable = bool(item.get("affordable", item.get("enough_gold", True)))
                stocked = bool(item.get("stocked", item.get("available", True)))
                if affordable and stocked:
                    idx = int(item.get("index", i) or i)
                    buyable.append(idx)
            if buyable:
                return {"action": "shop_purchase", "index": buyable[action_id % len(buyable)]}

        if "proceed" in legal:
            return {"action": "proceed"}
        return self._fallback_from_legal_actions(state)

    def _decode_event(self, action_id: int, state: Dict) -> Dict:
        legal = _get_legal_actions(state)
        event = state.get("event") or {}
        options = event.get("options") or []

        if "advance_dialogue" in legal and not options:
            return {"action": "advance_dialogue"}

        if options and "choose_event_option" in legal:
            idx = min(max(action_id, 0), len(options) - 1)
            return {"action": "choose_event_option", "index": idx}

        return self._fallback_from_legal_actions(state)

    def _decode_chest(self, action_id: int, state: Dict) -> Dict:
        chest = state.get("chest") or {}
        legal = _get_legal_actions(state)
        relics = chest.get("relic_options") or []
        idx = min(max(action_id, 0), len(relics) - 1) if relics else 0

        if relics and "claim_treasure_relic" in legal:
            return {"action": "claim_treasure_relic", "index": idx}
        if relics and "select_relic" in legal:
            return {"action": "select_relic", "index": idx}
        if "skip_relic_selection" in legal:
            return {"action": "skip_relic_selection"}
        if "proceed" in legal:
            return {"action": "proceed"}

        return self._fallback_from_legal_actions(state)

    def get_valid_action_mask(self, state: Dict) -> List[bool]:
        mask = [False] * self.total_actions
        if not _can_act_now(state):
            # Avoid invalid all-False distribution in policy forward.
            mask[0] = True
            return mask
        legal_actions = _get_legal_actions(state)
        if legal_actions:
            if "end_turn" in legal_actions:
                mask[self.max_hand + self.max_potions] = True

            if "play_card" in legal_actions:
                hand = (state.get("combat") or {}).get("hand", [])
                for i in range(min(len(hand), self.max_hand, self.total_actions)):
                    mask[i] = True

            if "use_potion" in legal_actions:
                for i in range(min(self.max_potions, self.total_actions - self.max_hand)):
                    mask[self.max_hand + i] = True

            if any(
                a in legal_actions for a in (
                    "choose_map_node", "choose_rest_option", "choose_event_option",
                    "select_card", "combat_select_card", "select_card_reward", "claim_treasure_relic",
                    "select_relic", "claim_reward", "skip_card_reward", "proceed", "confirm_selection",
                    "combat_confirm_selection", "shop_purchase", "open_character_select", "select_character",
                    "embark", "return_to_main_menu", "advance_dialogue", "choose_bundle",
                )
            ):
                for i in range(min(8, self.total_actions)):
                    if not mask[i]:
                        mask[i] = True

            if any(mask):
                return mask

        screen = state.get("screen_type", "NONE")
        combat = state.get("combat", {})
        energy = combat.get("energy", 0)
        hand = combat.get("hand", [])

        if screen == "COMBAT":
            for i, card in enumerate(hand[: self.max_hand]):
                cost = card.get("cost", 0)
                cost_val = cost if isinstance(cost, int) else 0
                if bool(card.get("playable", True)) and (cost_val <= energy or cost == "X"):
                    mask[i] = True
            potions = state.get("potions", [])
            for i in range(min(len(potions), self.max_potions)):
                mask[self.max_hand + i] = True
            mask[self.max_hand + self.max_potions] = True

        elif screen in ("CARD_REWARD", "REWARD", "MAP", "REST", "SHOP", "CARD_SELECT", "CHEST", "CHOOSE_CARD_BUNDLE", "EVENT"):
            for i in range(min(8, self.total_actions)):
                mask[i] = True

        return mask
