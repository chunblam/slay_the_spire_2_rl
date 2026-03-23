"""
src/env/sts2_env.py

STS2 Gymnasium-compatible Environment
通过 STS2MCP Mod 的 HTTP Raw API 与游戏交互（见 docs/STS2MCP-Raw-API-中文调用文档.md）
默认: GET/POST http://localhost:15526/api/v1/singleplayer
"""

import time
from typing import Any, Dict, List, Optional, Tuple

import gymnasium as gym
import numpy as np
import requests

from state_encoder import StateEncoder
from action_space import STS2ActionSpace

# Raw API 的 state_type → 本项目 encoder / action_space 使用的 screen_type
_STATE_TYPE_TO_LEGACY_SCREEN = {
    "monster": "COMBAT",
    "elite": "COMBAT",
    "boss": "COMBAT",
    "hand_select": "COMBAT",
    "map": "MAP",
    "card_reward": "CARD_REWARD",
    "rest_site": "REST",
    "shop": "SHOP",
    "event": "EVENT",
    "card_select": "CARD_SELECT",
    "relic_select": "BOSS_REWARD",
    "treasure": "CHEST",
    "combat_rewards": "OTHER",
    "overlay": "OTHER",
    "menu": "NONE",
}


class STS2Env(gym.Env):
    """
    杀戮尖塔2 强化学习环境

    Observation Space:
        Dict 空间，包含战斗状态、手牌、遗物等编码向量

    Action Space:
        Discrete 空间，动作包含:
        - 打出手牌 (0 ~ max_hand_size-1)
        - 使用药水 (max_hand_size ~ max_hand_size + max_potions - 1)
        - 结束回合 (last action)
    """

    metadata = {"render_modes": ["human", "rgb_array"]}

    def __init__(
        self,
        host: str = "localhost",
        port: int = 15526,
        api_mode: str = "singleplayer",
        max_hand_size: int = 10,
        max_potions: int = 5,
        render_mode: Optional[str] = None,
        timeout: float = 30.0,
    ):
        super().__init__()
        self.base_url = f"http://{host}:{port}"
        if api_mode not in ("singleplayer", "multiplayer"):
            raise ValueError('api_mode 须为 "singleplayer" 或 "multiplayer"')
        self.api_mode = api_mode
        self._api_path = f"/api/v1/{api_mode}"
        self.timeout = timeout
        self.render_mode = render_mode

        # 状态编码器 (将游戏 JSON 状态 → numpy 向量)
        self.encoder = StateEncoder()

        # 动作空间封装
        self.action_handler = STS2ActionSpace(max_hand_size, max_potions)

        # Gymnasium 标准接口
        self.observation_space = self.encoder.get_observation_space()
        self.action_space = gym.spaces.Discrete(self.action_handler.total_actions)

        # 内部状态
        self._current_state: Optional[Dict] = None
        self._episode_reward: float = 0.0
        self._step_count: int = 0
        self._in_combat: bool = False

    # ──────────────────────────────────────────────────────────────────────
    # Gymnasium 核心接口
    # ──────────────────────────────────────────────────────────────────────

    def reset(
        self, *, seed: Optional[int] = None, options: Optional[Dict] = None
    ) -> Tuple[Dict, Dict]:
        super().reset(seed=seed)
        self._episode_reward = 0.0
        self._step_count = 0

        # 等待游戏进入可操作状态
        state = self._wait_for_actionable_state()
        self._current_state = state

        obs = self.encoder.encode(state)
        info = self._build_info(state)
        return obs, info

    def step(self, action: int) -> Tuple[Dict, float, bool, bool, Dict]:
        assert self._current_state is not None, "请先调用 reset()"

        # 将 action id → 游戏 API 调用
        api_call = self.action_handler.decode(action, self._current_state)

        # 执行动作
        prev_state = self._current_state
        new_state = self._execute_action(api_call)
        self._current_state = new_state
        self._step_count += 1

        # 计算奖励
        reward, done = self._compute_reward(prev_state, new_state)
        self._episode_reward += reward

        obs = self.encoder.encode(new_state)
        info = self._build_info(new_state)
        info["action_executed"] = api_call

        truncated = self._step_count >= 1000  # 防止死循环
        return obs, reward, done, truncated, info

    def render(self):
        if self.render_mode == "human" and self._current_state:
            self._print_state(self._current_state)

    def close(self):
        pass

    # ──────────────────────────────────────────────────────────────────────
    # HTTP API 调用（对接 STS2MCP Mod）
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def _adapt_raw_state(state: Dict) -> Dict:
        """
        将 Raw API JSON 转为本项目内部仍使用的字段习惯：
        - battle → combat（供 StateEncoder）
        - state_type → screen_type（供 ActionSpace / 奖励里 screen 判断）
        """
        if not isinstance(state, dict):
            return state
        out = dict(state)
        if "battle" in out and "combat" not in out:
            out["combat"] = out["battle"]
        raw_st = out.get("state_type")
        if raw_st:
            out["screen_type"] = _STATE_TYPE_TO_LEGACY_SCREEN.get(raw_st, "OTHER")
        elif "screen_type" not in out:
            out["screen_type"] = "NONE"
        return out

    @staticmethod
    def _check_api_error(data: Any) -> None:
        if isinstance(data, dict) and data.get("status") == "error":
            raise RuntimeError(data.get("error", str(data)))

    def _get_state(self) -> Dict:
        """GET /api/v1/{singleplayer|multiplayer} — Raw API 完整状态"""
        url = f"{self.base_url}{self._api_path}"
        resp = requests.get(url, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        self._check_api_error(data)
        return self._adapt_raw_state(data)

    def _execute_action(self, post_body: Dict) -> Dict:
        """POST 与 GET 同一路径，body 内含 action 及参数（Raw API）"""
        if "action" not in post_body:
            raise ValueError(f"POST body 缺少 action 字段: {post_body!r}")

        url = f"{self.base_url}{self._api_path}"
        resp = requests.post(url, json=post_body, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        self._check_api_error(data)
        time.sleep(0.1)
        return self._get_state()

    def _wait_for_actionable_state(self, max_wait: float = 60.0) -> Dict:
        """等待游戏进入可操作状态（战斗、奖励、地图等）"""
        start = time.time()
        while time.time() - start < max_wait:
            try:
                state = self._get_state()
                screen_type = state.get("screen_type", "")
                if screen_type not in ("NONE", "GAME_OVER_LOADING", ""):
                    return state
            except requests.RequestException:
                pass
            time.sleep(0.5)
        raise TimeoutError(f"等待可操作状态超时 ({max_wait}s)")

    # ──────────────────────────────────────────────────────────────────────
    # 奖励计算（基础版，稠密奖励由 RewardShaper 增强）
    # ──────────────────────────────────────────────────────────────────────

    def _compute_reward(
        self, prev_state: Dict, new_state: Dict
    ) -> Tuple[float, bool]:
        """
        基础奖励函数，返回 (reward, done)

        奖励来源:
        1. 击杀敌人: +5.0 per enemy
        2. 玩家HP变化: -0.1 per HP lost
        3. 获得金币: +0.01 per gold
        4. 进入新楼层: +1.0
        5. 通关Boss: +50.0
        6. 死亡: -20.0
        """
        reward = 0.0
        done = False

        screen = new_state.get("screen_type", "")

        # 游戏结束
        if screen == "GAME_OVER":
            victory = new_state.get("game_over", {}).get("victory", False)
            reward += 50.0 if victory else -20.0
            done = True
            return reward, done

        # 战斗中奖励
        prev_combat = prev_state.get("combat", {})
        new_combat = new_state.get("combat", {})

        if prev_combat and new_combat:
            # HP 损失惩罚
            prev_hp = prev_combat.get("player", {}).get("hp", 0)
            new_hp = new_combat.get("player", {}).get("hp", 0)
            hp_loss = prev_hp - new_hp
            reward -= hp_loss * 0.1

            # 击杀敌人奖励
            prev_enemies = len([e for e in prev_combat.get("monsters", []) if e.get("hp", 0) > 0])
            new_enemies = len([e for e in new_combat.get("monsters", []) if e.get("hp", 0) > 0])
            kills = prev_enemies - new_enemies
            reward += kills * 5.0

        # 层数推进奖励
        prev_floor = prev_state.get("floor", 0)
        new_floor = new_state.get("floor", 0)
        if new_floor > prev_floor:
            reward += 1.0

        # 金币获取
        prev_gold = prev_state.get("gold", 0)
        new_gold = new_state.get("gold", 0)
        gold_gain = new_gold - prev_gold
        if gold_gain > 0:
            reward += gold_gain * 0.01

        return reward, done

    def _build_info(self, state: Dict) -> Dict:
        """构建 info 字典，供 LLM Advisor 使用"""
        return {
            "screen_type": state.get("screen_type", ""),
            "floor": state.get("floor", 0),
            "hp": state.get("combat", {}).get("player", {}).get("hp", 0),
            "max_hp": state.get("combat", {}).get("player", {}).get("max_hp", 0),
            "gold": state.get("gold", 0),
            "deck_size": len(state.get("deck", [])),
            "relics": [r.get("name") for r in state.get("relics", [])],
            "raw_state": state,
        }

    def _print_state(self, state: Dict):
        """Human render: 打印当前状态摘要"""
        screen = state.get("screen_type", "?")
        floor = state.get("floor", 0)
        combat = state.get("combat", {})
        player = combat.get("player", {})
        hp = player.get("hp", "?")
        max_hp = player.get("max_hp", "?")
        gold = state.get("gold", 0)

        print(f"\n[Floor {floor}] Screen: {screen} | HP: {hp}/{max_hp} | Gold: {gold}")

        if "monsters" in combat:
            for m in combat["monsters"]:
                name = m.get("name", "?")
                mhp = m.get("hp", 0)
                mmhp = m.get("max_hp", 0)
                intent = m.get("intent", {}).get("type", "?")
                print(f"  Enemy: {name} HP:{mhp}/{mmhp} Intent:{intent}")

        if "hand" in combat:
            hand = combat["hand"]
            print(f"  Hand ({len(hand)} cards):", [c.get("name", "?") for c in hand])