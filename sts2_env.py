import time
from typing import Any, Dict, List, Optional, Tuple

import gymnasium as gym
import requests

from state_encoder import StateEncoder
from action_space import STS2ActionSpace


class STS2Env(gym.Env):
    metadata = {"render_modes": ["human", "rgb_array"]}

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 15526,
        max_hand_size: int = 10,
        max_potions: int = 5,
        render_mode: Optional[str] = None,
        timeout: float = 30.0,
        character_index: int = 0,
        startup_debug: bool = False,
        action_poll_interval: float = 0.5,
        action_min_interval: float = 0.5,
        post_action_settle: float = 0.5,
        action_retry_count: int = 1,
        game_mode: str = "singleplayer",
    ):
        super().__init__()
        self.base_url = f"http://{host}:{port}"
        self.timeout = timeout
        self.render_mode = render_mode
        self.character_index = max(0, character_index)
        self.startup_debug = startup_debug
        self.action_poll_interval = max(0.1, action_poll_interval)
        self.action_min_interval = max(0.0, action_min_interval)
        self.post_action_settle = max(0.0, post_action_settle)
        self.action_retry_count = max(0, int(action_retry_count))
        self._last_action_at = 0.0

        mode = str(game_mode or "singleplayer").strip().lower()
        self._endpoint_path = "/api/v1/multiplayer" if mode == "multiplayer" else "/api/v1/singleplayer"

        self.encoder = StateEncoder()
        self.action_handler = STS2ActionSpace(
            max_hand_size=max_hand_size,
            max_potions=max_potions,
        )

        self.observation_space = self.encoder.get_observation_space()
        self.action_space = gym.spaces.Discrete(self.action_handler.total_actions)

        self._current_state: Optional[Dict] = None
        self._episode_reward: float = 0.0
        self._step_count: int = 0
        self._startup_character_selected: bool = False

    def reset(self, *, seed: Optional[int] = None, options: Optional[Dict] = None) -> Tuple[Dict, Dict]:
        super().reset(seed=seed)
        self._episode_reward = 0.0
        self._step_count = 0

        state = self._ensure_run_ready(timeout_sec=120.0)
        self._current_state = state

        obs = self.encoder.encode(state)
        info = self._build_info(state)
        return obs, info

    def step(self, action: int) -> Tuple[Dict, float, bool, bool, Dict]:
        assert self._current_state is not None, "reset() must be called first"

        prev_state = self._get_state()
        prev_state = self._wait_until_actionable_or_terminal(prev_state, max_wait=20.0)
        if not self._can_act_now(prev_state) and not self._is_terminal_state(prev_state):
            raise RuntimeError(
                "status=blocked can_act=false before action dispatch (state not actionable yet)"
            )
        self._current_state = prev_state

        api_call = self.action_handler.decode(action, prev_state)
        new_state = self._execute_action_with_recovery(api_call)
        # Wait for action to stabilize (API completes processing) before polling for next state
        new_state = self._wait_for_action_stable(new_state, max_wait=20.0)
        new_state = self._wait_until_actionable_or_terminal(new_state, max_wait=20.0)
        self._current_state = new_state
        self._step_count += 1

        reward, done = self._compute_reward(prev_state, new_state)
        self._episode_reward += reward

        obs = self.encoder.encode(new_state)
        info = self._build_info(new_state)
        info["action_executed"] = api_call

        truncated = self._step_count >= 1000
        return obs, reward, done, truncated, info

    def refresh_state(self) -> Tuple[Dict, Dict]:
        st = self._get_state()
        self._current_state = st
        return self.encoder.encode(st), self._build_info(st)

    def step_manual_intervention(
        self,
        prev_state: Dict,
        max_wait: float = 180.0,
        poll: Optional[float] = None,
    ) -> Tuple[Dict, float, bool, bool, Dict]:
        new_state, changed = self._wait_for_manual_state_change(prev_state, max_wait=max_wait, poll=poll)
        self._current_state = new_state
        self._step_count += 1

        reward, done = self._compute_reward(prev_state, new_state)
        self._episode_reward += reward
        obs = self.encoder.encode(new_state)
        info = self._build_info(new_state)
        info["action_executed"] = {"action": "manual_intervention"}
        info["manual_intervention"] = True
        info["manual_intervention_reason"] = "unknown_state_no_available_actions"
        info["manual_intervention_changed"] = changed
        info["manual_state_delta"] = self._build_state_delta(prev_state, new_state)
        truncated = self._step_count >= 1000
        return obs, reward, done, truncated, info

    def render(self):
        if self.render_mode == "human" and self._current_state:
            self._print_state(self._current_state)

    def close(self):
        pass

    @staticmethod
    def _unwrap_envelope(payload: Any) -> Dict:
        if not isinstance(payload, dict):
            return {}
        if "ok" in payload and "data" in payload:
            data = payload.get("data")
            return data if isinstance(data, dict) else {}
        if payload.get("status") == "error":
            if isinstance(payload.get("state"), dict):
                return payload.get("state")
            raise RuntimeError(str(payload.get("error", payload)))
        if isinstance(payload.get("state"), dict):
            return payload.get("state")
        return payload

    @staticmethod
    def _to_screen_type(state_type: str) -> str:
        st = str(state_type or "").lower()
        if st in ("monster", "elite", "boss"):
            return "COMBAT"
        if st == "map":
            return "MAP"
        if st == "card_reward":
            return "CARD_REWARD"
        if st == "combat_rewards":
            return "REWARD"
        if st == "rest_site":
            return "REST"
        if st == "shop":
            return "SHOP"
        if st == "event":
            return "EVENT"
        if st in ("treasure", "relic_select"):
            return "CHEST"
        if st in ("card_select", "hand_select"):
            return "CARD_SELECT"
        if st == "card_bundle":
            return "CHOOSE_CARD_BUNDLE"
        if st == "main_menu":
            return "NONE"
        if st in ("character_select_menu", "menu", "overlay", "unknown"):
            return "OTHER"
        return "OTHER"

    @staticmethod
    def _normalize_state(raw_state: Dict) -> Dict:
        out: Dict[str, Any] = {}
        state_type = str(raw_state.get("state_type", "")).lower()
        available_actions = [str(a) for a in (raw_state.get("available_actions") or [])]

        run = raw_state.get("run") or {}
        battle = raw_state.get("battle") or {}
        player = battle.get("player") or {}

        out["state_type"] = state_type
        out["raw_screen"] = state_type.upper()
        out["screen_type"] = STS2Env._to_screen_type(state_type)
        out["phase"] = "run" if state_type not in ("", "main_menu", "character_select_menu", "menu", "overlay", "unknown") else "menu"
        out["can_act"] = bool(available_actions)
        out["block_reason"] = None
        out["available_actions"] = available_actions
        out["legal_actions"] = available_actions

        out["floor"] = int(raw_state.get("floor", run.get("floor", 0)) or 0)
        out["gold"] = int(raw_state.get("gold", run.get("gold", 0)) or 0)
        out["deck"] = run.get("deck", raw_state.get("deck", [])) if isinstance(run.get("deck", raw_state.get("deck", [])), list) else []
        out["relics"] = run.get("relics", raw_state.get("relics", [])) if isinstance(run.get("relics", raw_state.get("relics", [])), list) else []
        out["potions"] = run.get("potions", raw_state.get("potions", [])) if isinstance(run.get("potions", raw_state.get("potions", [])), list) else []
        out["game_over"] = raw_state.get("game_over") or {}

        combat_payload: Dict[str, Any] = {}
        combat_payload["energy"] = int(player.get("energy", 0) or 0)
        combat_payload["max_energy"] = int(player.get("max_energy", battle.get("max_energy", 3)) or 3)
        combat_payload["round"] = int(battle.get("round", 0) or 0)
        combat_payload["turn"] = str(battle.get("turn", ""))
        combat_payload["combat_type"] = state_type if state_type in ("monster", "elite", "boss") else ""
        combat_payload["player"] = {
            "hp": int(player.get("hp", player.get("current_hp", 0)) or 0),
            "max_hp": int(player.get("max_hp", 1) or 1),
            "block": int(player.get("block", 0) or 0),
            "energy": int(player.get("energy", 0) or 0),
            "buffs": player.get("powers", player.get("buffs", [])) if isinstance(player.get("powers", player.get("buffs", [])), list) else [],
        }

        hand_source = player.get("hand") if isinstance(player.get("hand"), list) else battle.get("hand", [])
        hand_payload = []
        for idx, card in enumerate(hand_source or []):
            hand_payload.append({
                "index": idx,
                "name": card.get("name"),
                "cost": card.get("cost", card.get("energy_cost", 0)),
                "damage": card.get("damage", 0) or 0,
                "block": card.get("block", 0) or 0,
                "type": str(card.get("type", card.get("card_type", "ATTACK"))).upper(),
                "playable": bool(card.get("playable", card.get("can_play", True))),
                "requires_target": bool(card.get("requires_target", False)),
            })
        combat_payload["hand"] = hand_payload

        monsters_payload = []
        for idx, enemy in enumerate((battle.get("enemies") or [])):
            intent = enemy.get("intent") or {}
            monsters_payload.append({
                "index": idx,
                "name": enemy.get("name"),
                "id": enemy.get("entity_id", enemy.get("id")),
                "hp": int(enemy.get("hp", enemy.get("current_hp", 0)) or 0),
                "max_hp": int(enemy.get("max_hp", 1) or 1),
                "block": int(enemy.get("block", 0) or 0),
                "is_alive": bool(enemy.get("is_alive", (enemy.get("hp", 0) or 0) > 0)),
                "intent": {
                    "type": str(intent.get("type", intent.get("intent_type", "UNKNOWN"))).upper(),
                    "damage": int(intent.get("damage", 0) or 0),
                    "times": int(intent.get("times", 1) or 1),
                },
                "type": str(enemy.get("type", "")).upper(),
                "is_elite": bool(enemy.get("is_elite", False)),
                "is_boss": bool(enemy.get("is_boss", False)),
            })
        combat_payload["monsters"] = monsters_payload
        out["combat"] = combat_payload

        # Reward-like states
        rewards = raw_state.get("rewards") or {}
        reward_items = rewards.get("items", []) if isinstance(rewards, dict) else []
        out["reward"] = {"rewards": reward_items, "can_proceed": bool((rewards or {}).get("can_proceed", False))}

        card_reward = raw_state.get("card_reward") or {}
        card_reward_cards = card_reward.get("cards", []) if isinstance(card_reward, dict) else []
        out["card_reward"] = {"cards": card_reward_cards, "can_skip": bool((card_reward or {}).get("can_skip", False))}

        # Map
        map_payload = raw_state.get("map") or {}
        out["map"] = {
            "next_options": map_payload.get("next_options", map_payload.get("available_nodes", [])) if isinstance(map_payload.get("next_options", map_payload.get("available_nodes", [])), list) else [],
            "nodes": map_payload.get("nodes", []) if isinstance(map_payload.get("nodes", []), list) else [],
        }

        out["rest"] = raw_state.get("rest_site") or raw_state.get("rest") or {}

        # Shop: normalize item categories for decoder
        shop = raw_state.get("shop") or {}
        shop_items = shop.get("items", []) if isinstance(shop.get("items", []), list) else []
        cards, relics, potions = [], [], []
        removal = None
        for i, it in enumerate(shop_items):
            if not isinstance(it, dict):
                continue
            cat = str(it.get("category", "")).lower()
            idx = int(it.get("index", i) or i)
            norm = dict(it)
            norm["index"] = idx
            if cat == "card":
                cards.append(norm)
            elif cat == "relic":
                relics.append(norm)
            elif cat == "potion":
                potions.append(norm)
            elif cat == "card_removal":
                removal = norm
        out["shop"] = {
            **shop,
            "cards": cards,
            "relics": relics,
            "potions": potions,
            "card_removal": removal or {},
        }

        out["event"] = raw_state.get("event") or {}

        treasure = raw_state.get("treasure") or {}
        relic_select = raw_state.get("relic_select") or {}
        chest_relics = []
        if isinstance(treasure.get("relics"), list):
            chest_relics = treasure.get("relics")
        elif isinstance(relic_select.get("relics"), list):
            chest_relics = relic_select.get("relics")
        out["chest"] = {
            "is_opened": bool(chest_relics),
            "relic_options": chest_relics,
            "can_proceed": bool(treasure.get("can_proceed", relic_select.get("can_skip", False))),
        }

        card_select = raw_state.get("card_select") or {}
        hand_select = raw_state.get("hand_select") or {}
        select_cards = card_select.get("cards") if isinstance(card_select.get("cards"), list) else hand_select.get("cards", [])
        out["selection"] = {
            "cards": select_cards if isinstance(select_cards, list) else [],
            "prompt": card_select.get("prompt", hand_select.get("prompt", "")),
            "can_confirm": bool(card_select.get("can_confirm", hand_select.get("can_confirm", False))),
            "can_cancel": bool(card_select.get("can_cancel", hand_select.get("can_cancel", False))),
        }

        out["card_bundle"] = raw_state.get("card_bundle") if isinstance(raw_state.get("card_bundle"), dict) else {}

        return out

    def _get_state(self) -> Dict:
        resp = requests.get(f"{self.base_url}{self._endpoint_path}", timeout=self.timeout)
        resp.raise_for_status()
        payload = self._unwrap_envelope(resp.json())
        return self._normalize_state(payload)

    def _post_action(self, body: Dict) -> Dict:
        if "action" not in body:
            raise ValueError(f"POST body missing 'action': {body!r}")
        self._throttle_action_if_needed()
        resp = requests.post(f"{self.base_url}{self._endpoint_path}", json=body, timeout=self.timeout)
        if resp.status_code >= 400:
            raise RuntimeError(
                f"action failed: status={resp.status_code}, body={body}, response={resp.text}"
            )
        payload = resp.json()
        data = self._unwrap_envelope(payload)
        self._last_action_at = time.time()
        if self.post_action_settle > 0:
            time.sleep(self.post_action_settle)
        return self._normalize_state(data)

    def _execute_action_with_recovery(self, body: Dict, max_retries: Optional[int] = None) -> Dict:
        max_retries = self.action_retry_count if max_retries is None else max(0, max_retries)
        last_err = ""
        for _ in range(max_retries + 1):
            try:
                return self._post_action(body)
            except Exception as ex:
                last_err = str(ex)
                time.sleep(self.action_poll_interval)
        raise RuntimeError(f"action failed after retries: {body} | {last_err}")

    def _ensure_run_ready(self, timeout_sec: float = 120.0) -> Dict:
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            st = self._get_state()
            state_type = str(st.get("state_type", "")).lower()
            legal = [str(a) for a in (st.get("legal_actions") or [])]

            if self.startup_debug:
                print(f"[startup] state_type={state_type} legal={legal}")

            # Already in a playable run-state
            if state_type in (
                "monster", "elite", "boss", "map", "event", "shop", "rest_site",
                "card_reward", "combat_rewards", "card_select", "hand_select",
                "card_bundle", "treasure", "relic_select",
            ):
                self._startup_character_selected = False
                return st

            # Menu bootstrap to new run
            if "open_character_select" in legal:
                self._post_action({"action": "open_character_select"})
                self._startup_character_selected = False
                continue

            if "select_character" in legal and not self._startup_character_selected:
                self._post_action({"action": "select_character", "index": self.character_index})
                self._startup_character_selected = True
                continue

            if "embark" in legal:
                self._post_action({"action": "embark"})
                continue

            if "return_to_main_menu" in legal and state_type != "main_menu":
                self._post_action({"action": "return_to_main_menu"})
                self._startup_character_selected = False
                continue

            time.sleep(self.action_poll_interval)

        raise TimeoutError("timed out waiting for run-ready state")

    @staticmethod
    def _state_is_actionable(state: Dict) -> bool:
        return bool(state.get("screen_type", "")) and state.get("screen_type") != "NONE"

    @staticmethod
    def _can_act_now(state: Dict) -> bool:
        return bool(state.get("can_act", True))

    def _wait_for_manual_state_change(
        self,
        prev_state: Dict,
        max_wait: float = 180.0,
        poll: Optional[float] = None,
    ) -> Tuple[Dict, bool]:
        poll = self.action_poll_interval if poll is None else max(0.1, poll)
        start = time.time()
        baseline_sig = self._state_signature(prev_state)
        last_state = prev_state
        while time.time() - start < max_wait:
            st = self._get_state()
            last_state = st
            if self._state_signature(st) != baseline_sig:
                return st, True
            time.sleep(poll)
        return last_state, False

    def _wait_until_actionable_or_terminal(
        self,
        start_state: Dict,
        max_wait: float = 20.0,
        poll: Optional[float] = None,
    ) -> Dict:
        if self._can_act_now(start_state) or self._is_terminal_state(start_state):
            return start_state

        poll = self.action_poll_interval if poll is None else max(0.1, poll)
        deadline = time.time() + max(0.1, max_wait)
        last_state = start_state
        while time.time() < deadline:
            st = self._get_state()
            last_state = st
            if self._can_act_now(st) or self._is_terminal_state(st):
                return st
            time.sleep(poll)
        return last_state

    def _wait_for_action_stable(
        self,
        start_state: Dict,
        max_wait: float = 20.0,
        poll: Optional[float] = None,
    ) -> Dict:
        """
        Waits for action to stabilize (API processing complete).
        This avoids the multiple-POST problem by ensuring the API has processed 
        the action before the RL environment polls for the next state.
        
        Stability is indicated by the "stable" flag in the response.
        If not present, returns immediately (assumes stable).
        """
        if start_state.get("stable", True):
            return start_state

        poll = self.action_poll_interval if poll is None else max(0.1, poll)
        deadline = time.time() + max(0.1, max_wait)
        last_state = start_state
        
        while time.time() < deadline:
            st = self._get_state()
            last_state = st
            if st.get("stable", True):
                return st
            time.sleep(poll)
        
        return last_state

    @staticmethod
    def _state_signature(state: Dict) -> Tuple:
        combat_player = (state.get("combat") or {}).get("player") or {}
        return (
            state.get("screen_type", ""),
            state.get("state_type", ""),
            bool(state.get("can_act", False)),
            tuple(state.get("legal_actions") or []),
            int(state.get("floor", 0) or 0),
            int(state.get("gold", 0) or 0),
            int(combat_player.get("hp", 0) or 0),
            int(combat_player.get("block", 0) or 0),
            len((state.get("deck") or [])),
            len((state.get("relics") or [])),
        )

    @staticmethod
    def _is_terminal_state(state: Dict) -> bool:
        screen = str(state.get("screen_type", "")).upper()
        if screen == "GAME_OVER":
            return True
        state_type = str(state.get("state_type", "")).lower()
        if state_type in ("main_menu", "character_select_menu", "menu"):
            return True
        game_over = state.get("game_over") or {}
        return bool(game_over.get("victory", False) or game_over.get("defeat", False))

    @staticmethod
    def _build_state_delta(prev_state: Dict, new_state: Dict) -> Dict:
        prev_player = (prev_state.get("combat") or {}).get("player") or {}
        new_player = (new_state.get("combat") or {}).get("player") or {}
        return {
            "screen_type": [prev_state.get("screen_type", ""), new_state.get("screen_type", "")],
            "state_type": [prev_state.get("state_type", ""), new_state.get("state_type", "")],
            "can_act": [bool(prev_state.get("can_act", False)), bool(new_state.get("can_act", False))],
            "legal_actions_count": [len(prev_state.get("legal_actions") or []), len(new_state.get("legal_actions") or [])],
            "floor": [int(prev_state.get("floor", 0) or 0), int(new_state.get("floor", 0) or 0)],
            "gold": [int(prev_state.get("gold", 0) or 0), int(new_state.get("gold", 0) or 0)],
            "hp": [int(prev_player.get("hp", 0) or 0), int(new_player.get("hp", 0) or 0)],
            "deck_size": [len(prev_state.get("deck") or []), len(new_state.get("deck") or [])],
            "relic_count": [len(prev_state.get("relics") or []), len(new_state.get("relics") or [])],
        }

    def _throttle_action_if_needed(self):
        if self.action_min_interval <= 0:
            return
        elapsed = time.time() - self._last_action_at
        remain = self.action_min_interval - elapsed
        if remain > 0:
            time.sleep(remain)

    def _compute_reward(self, prev_state: Dict, new_state: Dict) -> Tuple[float, bool]:
        reward = 0.0
        done = False

        if prev_state.get("screen_type") == "GAME_OVER":
            done = True
            return reward, done

        if new_state.get("screen_type") == "GAME_OVER":
            done = True
            return reward, done

        # In STS2MCP, terminal transitions may return to menu states directly.
        prev_state_type = str(prev_state.get("state_type", "")).lower()
        new_state_type = str(new_state.get("state_type", "")).lower()
        if prev_state_type not in ("main_menu", "character_select_menu") and new_state_type in ("main_menu", "character_select_menu"):
            done = True
            return reward, done

        game_over = new_state.get("game_over") or {}
        if bool(game_over.get("victory", False)) or bool(game_over.get("defeat", False)):
            done = True
            return reward, done

        return reward, done

    def _build_info(self, state: Dict) -> Dict:
        return {
            "screen_type": state.get("screen_type", ""),
            "state_type": state.get("state_type", ""),
            "floor": state.get("floor", 0),
            "hp": state.get("combat", {}).get("player", {}).get("hp", 0),
            "max_hp": state.get("combat", {}).get("player", {}).get("max_hp", 0),
            "gold": state.get("gold", 0),
            "deck_size": len(state.get("deck", [])),
            "relics": [r.get("name") for r in state.get("relics", []) if isinstance(r, dict)],
            "legal_actions": state.get("legal_actions", []),
            "available_actions": state.get("available_actions", []),
            "raw_state": state,
        }

    def _print_state(self, state: Dict):
        screen = state.get("screen_type", "?")
        floor = state.get("floor", 0)
        combat = state.get("combat", {})
        player = combat.get("player", {})
        hp = player.get("hp", "?")
        max_hp = player.get("max_hp", "?")
        gold = state.get("gold", 0)
        print(f"\\n[Floor {floor}] Screen: {screen} | HP: {hp}/{max_hp} | Gold: {gold}")
