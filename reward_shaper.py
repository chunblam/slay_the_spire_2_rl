"""
Reward v2: A/B/C/D/E 分层奖励（严格对齐设计文档版）

修复清单（对照 ChatGPT 评估报告）：
[P0] ① CombatTracker._detect_enemy_type: 改为读取 monster["type"] 字段，不再用敌人数量启发
[P0] ② Layer C 奖励数值对齐文档：NORMAL=3 / ELITE=8+HP效率+精英额外 / BOSS=20+10
[P0] ③ Layer B B4 HP损失：使用 end_turn 时的快照 vs 敌人行动后状态，而非 prev→new
[P1] ④ Layer B B1 伤害效率：改为基于 max_hp 的自适应归一化（文档公式）
[P1] ⑤ Layer B B2 格挡覆盖：改用 end_turn 时的实际 block 值而非累计 acc_block_gain
[P1] ⑥ Layer B B3 能量利用率：改为 wasted_energy / max_energy（能量比，非手牌比）
[P2] ⑦ Layer A：补充 use_potion +0.05
[P2] ⑧ Layer D：补充 buy_card / buy_relic / buy_potion，加入 META_WEIGHT=0.3 缩放
[P2] ⑨ Layer D：休息 HP判断改为读 prev_state（行动前），而非 new_state
[P2] ⑩ Layer C：HP效率奖励改为 (1-hp_lost_ratio)*4.0（文档公式），不再用差值×2
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

try:
    from src.llm.llm_advisor import LLMAdvisor
except ImportError:
    try:
        from llm_advisor import LLMAdvisor
    except ImportError:
        LLMAdvisor = None  # type: ignore


# ──────────────────────────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────────────────────────

def _sum_enemy_hp(state: Dict) -> float:
    combat = state.get("combat") or {}
    return float(sum(
        (m.get("hp", 0) or 0)
        for m in (combat.get("monsters") or [])
    ))


def _alive_enemy_count(state: Dict) -> int:
    combat = state.get("combat") or {}
    return len([
        m for m in (combat.get("monsters") or [])
        if (m.get("hp", 0) or 0) > 0 and m.get("is_alive", True)
    ])


def _player(state: Dict) -> Dict:
    return ((state.get("combat") or {}).get("player") or {})


def _player_hp(state: Dict) -> float:
    return float(_player(state).get("hp", 0) or 0)


def _player_max_hp(state: Dict) -> float:
    return max(float(_player(state).get("max_hp", 1) or 1), 1.0)


def _player_hp_ratio(state: Dict) -> float:
    hp = _player_hp(state)
    max_hp = _player_max_hp(state)
    return max(0.0, min(1.0, hp / max_hp))


def _sum_enemy_intent_damage(state: Dict) -> float:
    """计算当前回合敌人意图的总伤害（含多段攻击）"""
    dmg = 0.0
    monsters = ((state.get("combat") or {}).get("monsters") or [])
    for m in monsters:
        if (m.get("hp", 0) or 0) <= 0:
            continue  # 跳过已死亡敌人
        intent = m.get("intent") or {}
        intent_type = str(intent.get("type", "")).upper()
        if intent_type in ("ATTACK", "ATTACK_BUFF", "ATTACK_DEBUFF"):
            base_dmg = float(intent.get("damage", 0) or 0)
            times = int(intent.get("times", 1) or 1)
            dmg += base_dmg * times
    return dmg


# ──────────────────────────────────────────────────────────────────
# TurnTracker：回合级数据累积
# ──────────────────────────────────────────────────────────────────

@dataclass
class TurnTracker:
    """
    追踪单个回合内的累积数据，供 Layer B 回合结算使用。

    关键字段：
    - turn_start_snapshot : 回合开始时（抽牌后）的完整状态，用于 B1/B4
    - end_turn_snapshot   : 玩家按 end_turn 后（敌人行动前）的状态，用于 B2/B3
    - post_enemy_snapshot : 敌人行动完毕，新回合开始前的状态，用于 B4 HP差分
    """
    in_combat: bool = False
    turn_number: int = 0

    # 状态快照（修复 P0-③）
    turn_start_snapshot: Optional[Dict] = None
    end_turn_snapshot: Optional[Dict] = None
    post_enemy_snapshot: Optional[Dict] = None  # 敌人行动后（下回合开始前）

    # 用于 combat_start 时的初始化备用
    expected_enemy_damage: float = 0.0
    acc_kills: int = 0

    def on_combat_start(self, state: Dict):
        self.in_combat = True
        self.turn_number = 0
        self.on_turn_start(state)

    def on_turn_start(self, state: Dict):
        """每回合开始（玩家抽牌后）调用"""
        self.turn_number += 1
        self.turn_start_snapshot = state
        self.end_turn_snapshot = None
        self.post_enemy_snapshot = None
        self.expected_enemy_damage = _sum_enemy_intent_damage(state)
        self.acc_kills = 0

    def on_end_turn(self, state: Dict):
        """玩家按下 end_turn 后，敌人行动前调用"""
        self.end_turn_snapshot = state

    def on_enemy_turn_end(self, state: Dict):
        """敌人行动完毕，新回合抽牌前调用"""
        self.post_enemy_snapshot = state

    def accumulate_kills(self, prev_state: Dict, new_state: Dict):
        prev_alive = _alive_enemy_count(prev_state)
        new_alive = _alive_enemy_count(new_state)
        self.acc_kills += max(prev_alive - new_alive, 0)


# ──────────────────────────────────────────────────────────────────
# CombatTracker：战斗级数据
# ──────────────────────────────────────────────────────────────────

@dataclass
class CombatTracker:
    in_combat: bool = False
    combat_start_state: Optional[Dict] = None
    combat_start_hp_ratio: float = 1.0
    combat_start_floor: int = 0
    combat_enemy_type: str = "NORMAL"   # NORMAL / ELITE / BOSS
    combat_turns: int = 0

    def on_combat_start(self, state: Dict):
        self.in_combat = True
        self.combat_start_state = state
        self.combat_start_hp_ratio = _player_hp_ratio(state)
        self.combat_start_floor = int(state.get("floor", 0) or 0)
        self.combat_enemy_type = self._detect_enemy_type(state)
        self.combat_turns = 0

    @staticmethod
    def _detect_enemy_type(state: Dict) -> str:
        """
        [修复 P0-①] 读取 monster 的 type 字段判断敌人类型。
        优先级：BOSS > ELITE > NORMAL
        兼容字段名：type / enemy_type / is_boss / is_elite
        """
        monsters = ((state.get("combat") or {}).get("monsters") or [])
        for m in monsters:
            if (m.get("hp", 0) or 0) <= 0:
                continue
            # 优先读取标准 type 字段
            mtype = str(m.get("type", "") or "").upper()
            if mtype == "BOSS":
                return "BOSS"
            if mtype == "ELITE":
                return "ELITE"
            # 兼容布尔字段
            if m.get("is_boss", False):
                return "BOSS"
            if m.get("is_elite", False):
                return "ELITE"
        return "NORMAL"


# ──────────────────────────────────────────────────────────────────
# RewardShaper 主类
# ──────────────────────────────────────────────────────────────────

class RewardShaper:
    """
    五层奖励架构：
      A - 动作级即时（每步，幅度小）
      B - 回合级结算（end_turn 触发，幅度中，核心）
      C - 战斗级结算（战斗结束触发，幅度大）
      D - 局外阶段（非战斗屏幕，权重×0.3）
      E - 终局奖励（done=True，幅度最大）

    量级设计目标：|E| >> |C| > |B| >= |D*0.3| > |A|
    """

    def __init__(
        self,
        llm_advisor=None,
        llm_weight: float = 0.3,
        # 层权重
        layer_a_weight: float = 1.0,
        layer_b_weight: float = 1.0,
        layer_c_weight: float = 1.0,
        layer_d_weight: float = 0.3,   # [修复 P2-⑧] 文档要求 META_WEIGHT=0.3
        layer_e_weight: float = 1.0,
        # Layer A
        action_damage_coef: float = 0.004,
        action_block_coef: float = 0.002,
        action_card_pick_bonus: float = 0.05,
        action_potion_bonus: float = 0.05,   # [修复 P2-⑦] 新增药水使用奖励
        # Layer B
        dmg_reward_cap: float = 1.5,
        kill_reward_per_enemy: float = 2.0,
        block_coverage_reward: float = 1.0,
        excess_block_penalty_cap: float = 0.2,
        energy_waste_penalty: float = 0.5,
        hp_loss_penalty: float = 1.5,
        hp_loss_urgency_max_mul: float = 2.0,  # 濒死时最大惩罚倍数
        # Layer C [修复 P0-②]
        normal_combat_bonus: float = 3.0,
        elite_combat_bonus: float = 8.0,
        boss_combat_bonus: float = 20.0,
        boss_extra_bonus: float = 10.0,
        hp_efficiency_max: float = 4.0,
        elite_clean_bonus: float = 3.0,    # 精英战HP损失<30%额外奖励
        elite_clean_threshold: float = 0.3,
        # Layer D [修复 P2-⑧⑨]
        rest_low_hp_bonus: float = 1.0,
        rest_mid_hp_bonus: float = 0.3,
        rest_high_hp_penalty: float = 0.5,
        rest_low_threshold: float = 0.35,
        rest_mid_threshold: float = 0.6,
        rest_high_threshold: float = 0.8,
        smith_bonus: float = 0.5,
        remove_card_bonus: float = 0.4,
        choose_card_meta_bonus: float = 0.3,
        buy_bonus: float = 0.2,            # [修复 P2-⑧] 商店购买奖励
        # Layer E
        terminal_victory_bonus: float = 100.0,
        terminal_defeat_penalty: float = 30.0,
        terminal_floor_weight: float = 1.5,
        terminal_hp_quality_weight: float = 10.0,
        # LLM match
        confidence_threshold: float = 0.55,
        card_weight: float = 0.4,
        card_match_bonus: float = 1.0,
        card_mismatch_penalty: float = 0.5,
        relic_choice_weight: float = 0.25,
        map_route_weight: float = 0.25,
        combat_opening_weight: float = 0.2,
        combat_bias_steps: int = 3,
        reward_clip: float = 50.0,
    ):
        self.llm_advisor = llm_advisor
        self.llm_weight = llm_weight

        self.layer_a_weight = layer_a_weight
        self.layer_b_weight = layer_b_weight
        self.layer_c_weight = layer_c_weight
        self.layer_d_weight = layer_d_weight
        self.layer_e_weight = layer_e_weight

        self.action_damage_coef = action_damage_coef
        self.action_block_coef = action_block_coef
        self.action_card_pick_bonus = action_card_pick_bonus
        self.action_potion_bonus = action_potion_bonus

        self.dmg_reward_cap = dmg_reward_cap
        self.kill_reward_per_enemy = kill_reward_per_enemy
        self.block_coverage_reward = block_coverage_reward
        self.excess_block_penalty_cap = excess_block_penalty_cap
        self.energy_waste_penalty = energy_waste_penalty
        self.hp_loss_penalty = hp_loss_penalty
        self.hp_loss_urgency_max_mul = hp_loss_urgency_max_mul

        self.normal_combat_bonus = normal_combat_bonus
        self.elite_combat_bonus = elite_combat_bonus
        self.boss_combat_bonus = boss_combat_bonus
        self.boss_extra_bonus = boss_extra_bonus
        self.hp_efficiency_max = hp_efficiency_max
        self.elite_clean_bonus = elite_clean_bonus
        self.elite_clean_threshold = elite_clean_threshold

        self.rest_low_hp_bonus = rest_low_hp_bonus
        self.rest_mid_hp_bonus = rest_mid_hp_bonus
        self.rest_high_hp_penalty = rest_high_hp_penalty
        self.rest_low_threshold = rest_low_threshold
        self.rest_mid_threshold = rest_mid_threshold
        self.rest_high_threshold = rest_high_threshold
        self.smith_bonus = smith_bonus
        self.remove_card_bonus = remove_card_bonus
        self.choose_card_meta_bonus = choose_card_meta_bonus
        self.buy_bonus = buy_bonus

        self.terminal_victory_bonus = terminal_victory_bonus
        self.terminal_defeat_penalty = terminal_defeat_penalty
        self.terminal_floor_weight = terminal_floor_weight
        self.terminal_hp_quality_weight = terminal_hp_quality_weight

        self.confidence_threshold = confidence_threshold
        self.card_weight = card_weight
        self.card_match_bonus = card_match_bonus
        self.card_mismatch_penalty = card_mismatch_penalty
        self.relic_choice_weight = relic_choice_weight
        self.map_route_weight = map_route_weight
        self.combat_opening_weight = combat_opening_weight
        self.combat_bias_steps = max(1, int(combat_bias_steps))
        self.reward_clip = abs(float(reward_clip))

        self.turn_tracker = TurnTracker()
        self.combat_tracker = CombatTracker()
        self.last_breakdown: Dict[str, float] = {}

        # 内部状态：用于检测"敌人回合结束"的时机
        self._waiting_for_enemy_turn_end: bool = False

    # ──────────────────────────────────────────────────────────────
    # 主入口
    # ──────────────────────────────────────────────────────────────

    def shape(
        self,
        base_reward: float,
        prev_state: Dict,
        new_state: Dict,
        action: Dict,
        done: bool,
        agent_card_index: Optional[int] = None,
        agent_relic_index: Optional[int] = None,
        agent_map_index: Optional[int] = None,
        combat_step: Optional[int] = None,
        agent_card_played: Optional[int] = None,
    ) -> float:
        _ = base_reward  # v2 完全不使用 env.base_reward
        kind = str(action.get("action") or action.get("type") or "")
        prev_screen = str(prev_state.get("screen_type", ""))
        new_screen = str(new_state.get("screen_type", ""))

        # ── 生命周期钩子 ─────────────────────────────────────────
        combat_start = (prev_screen != "COMBAT" and new_screen == "COMBAT")
        combat_end   = (prev_screen == "COMBAT" and new_screen != "COMBAT")

        if combat_start:
            self.combat_tracker.on_combat_start(new_state)
            self.turn_tracker.on_combat_start(new_state)
            self._waiting_for_enemy_turn_end = False

        # 检测"敌人回合结束"：
        # 标志：上一步玩家按了 end_turn（_waiting_for_enemy_turn_end=True），
        # 本步回合已推进（能量恢复 or 回合标记变化）
        if self._waiting_for_enemy_turn_end and new_screen == "COMBAT":
            if self._is_new_player_turn(self.turn_tracker.end_turn_snapshot, new_state):
                self.turn_tracker.on_enemy_turn_end(new_state)
                self._waiting_for_enemy_turn_end = False
                # 此时 post_enemy_snapshot 已就绪，on_turn_start 将在下面执行
                self.turn_tracker.on_turn_start(new_state)

        # 杀敌累积（每步战斗内）
        if self.turn_tracker.in_combat and new_screen == "COMBAT":
            self.turn_tracker.accumulate_kills(prev_state, new_state)

        # ── Layer A ───────────────────────────────────────────────
        a = self.layer_a_action_reward(prev_state, new_state, kind)

        # ── Layer B（回合结算，仅 end_turn 触发）─────────────────
        b = 0.0
        if self.turn_tracker.in_combat and kind == "end_turn":
            # 先记录 end_turn 快照
            self.turn_tracker.on_end_turn(new_state)
            self._waiting_for_enemy_turn_end = True
            self.combat_tracker.combat_turns += 1
            # B 的结算在 post_enemy_snapshot 就绪后进行
            # 此处先做"不依赖 post_enemy" 的 B1/B2/B3 分项
            b = self._layer_b_partial(prev_state, new_state)

        # ── Layer B-B4（敌人行动后补算，追加到上一步奖励）────────
        # 实现上：在检测到新回合时，将 B4 加到本步
        # （因为 PPO rollout 是逐步存储，我们选择在"新回合检测步"给出 B4 奖励）
        b4_deferred = 0.0
        if (not self._waiting_for_enemy_turn_end and
                self.turn_tracker.post_enemy_snapshot is not None and
                self.turn_tracker.end_turn_snapshot is not None):
            b4_deferred = self._layer_b4_hp_loss()
            # 重置，避免重复计算
            self.turn_tracker.end_turn_snapshot = None
            self.turn_tracker.post_enemy_snapshot = None
        b += b4_deferred

        # ── Layer C（战斗结算）────────────────────────────────────
        c = 0.0
        if combat_end:
            is_victory = bool((new_state.get("game_over") or {}).get("victory", False))
            c = self.layer_c_combat_reward(new_state, is_victory)
            self.turn_tracker.in_combat = False
            self.combat_tracker.in_combat = False

        # ── Layer D（局外阶段）────────────────────────────────────
        d = self.layer_d_meta_reward(prev_state, new_state, action)

        # ── Layer E（终局）───────────────────────────────────────
        e = self.layer_e_terminal_reward(new_state, done)

        total = (
            self.layer_a_weight * a
            + self.layer_b_weight * b
            + self.layer_c_weight * c
            + self.layer_d_weight * d   # layer_d_weight=0.3 已内置 META_WEIGHT
            + self.layer_e_weight * e
        )

        # ── LLM 全局路线奖励 ─────────────────────────────────────
        llm_route = 0.0
        if (self.llm_advisor is not None
                and self._should_query_llm(new_screen)
                and new_screen != "CARD_REWARD"):
            llm_route = self.llm_advisor.get_reward_shaping_bonus(new_state)
            total += self.llm_weight * llm_route

        # ── LLM match 奖励 ────────────────────────────────────────
        llm_card  = self._compute_card_match_bonus(agent_card_index)    if agent_card_index  is not None else 0.0
        llm_relic = self._compute_relic_match_bonus(agent_relic_index)  if agent_relic_index is not None else 0.0
        llm_map   = self._compute_map_match_bonus(agent_map_index)      if agent_map_index   is not None else 0.0
        llm_open  = self._compute_combat_opening_bonus(combat_step, agent_card_played)

        total += self.card_weight          * llm_card
        total += self.relic_choice_weight  * llm_relic
        total += self.map_route_weight     * llm_map
        total += self.combat_opening_weight * llm_open

        # 失效缓存
        if agent_card_index  is not None and self.llm_advisor is not None:
            self.llm_advisor.invalidate_card_recommendation()
        if agent_relic_index is not None and self.llm_advisor is not None:
            self.llm_advisor.invalidate_relic_recommendation()
        if agent_map_index   is not None and self.llm_advisor is not None:
            self.llm_advisor.invalidate_map_recommendation()

        if self.reward_clip > 0:
            total = max(min(total, self.reward_clip), -self.reward_clip)

        self.last_breakdown = {
            "A_action":     float(a),
            "B_turn":       float(b),
            "C_combat":     float(c),
            "D_meta":       float(d),
            "E_terminal":   float(e),
            "LLM_route":    float(llm_route),
            "LLM_card":     float(llm_card),
            "LLM_relic":    float(llm_relic),
            "LLM_map":      float(llm_map),
            "LLM_opening":  float(llm_open),
            "total":        float(total),
        }
        return total

    # ──────────────────────────────────────────────────────────────
    # Layer A：动作级即时奖励
    # ──────────────────────────────────────────────────────────────

    def layer_a_action_reward(
        self, prev_state: Dict, new_state: Dict, action_kind: str
    ) -> float:
        reward = 0.0

        if action_kind == "play_card":
            # A1: 伤害即时项
            dmg = max(_sum_enemy_hp(prev_state) - _sum_enemy_hp(new_state), 0.0)
            reward += dmg * self.action_damage_coef  # 25伤 → +0.10

            # A2: 格挡即时项
            prev_blk = float((_player(prev_state)).get("block", 0) or 0)
            new_blk  = float((_player(new_state)).get("block",  0) or 0)
            reward += max(new_blk - prev_blk, 0.0) * self.action_block_coef  # 25格挡 → +0.05

        elif action_kind == "use_potion":
            # [修复 P2-⑦] 使用药水给小正奖励，避免 AI 囤药不用
            reward += self.action_potion_bonus

        elif action_kind == "choose_reward_card":
            reward += self.action_card_pick_bonus

        return reward

    # ──────────────────────────────────────────────────────────────
    # Layer B：回合级结算奖励
    # ──────────────────────────────────────────────────────────────

    def _layer_b_partial(self, prev_state: Dict, end_state: Dict) -> float:
        """
        B1 / B2 / B3 三项（不依赖 post_enemy_snapshot）。
        在玩家按 end_turn 时立即计算。
        B4（HP损失）延迟到敌人行动后在 _layer_b4_hp_loss() 中计算。

        参数：
          prev_state : end_turn 动作的 prev_state（玩家最后一步前）
          end_state  : end_turn 动作的 new_state（玩家回合结束，敌人未行动）
        """
        reward = 0.0
        snap = self.turn_tracker.turn_start_snapshot or end_state
        snap_combat = snap.get("combat") or {}
        end_combat  = end_state.get("combat") or {}
        snap_player = snap_combat.get("player") or {}
        end_player  = end_combat.get("player") or {}

        max_hp = max(float(snap_player.get("max_hp", 80) or 80), 1.0)

        # ── B1: 本回合总伤害效率 [修复 P1-④] ────────────────────
        # 文档公式：dmg_eff = total_dmg / (max_hp * 0.1)
        #           reward += min(dmg_eff * 0.8, 1.5)
        snap_enemy_hp = float(sum(
            (m.get("hp", 0) or 0)
            for m in (snap_combat.get("monsters") or [])
            if (m.get("hp", 0) or 0) > 0
        ))
        end_enemy_hp = float(sum(
            (m.get("hp", 0) or 0)
            for m in (end_combat.get("monsters") or [])
            if (m.get("hp", 0) or 0) > 0
        ))
        total_dmg_this_turn = max(snap_enemy_hp - end_enemy_hp, 0.0)
        dmg_efficiency = total_dmg_this_turn / (max_hp * 0.1)
        reward += min(dmg_efficiency * 0.8, self.dmg_reward_cap)

        # B1a: 回合内击杀奖励
        reward += self.turn_tracker.acc_kills * self.kill_reward_per_enemy

        # ── B2: 格挡覆盖率 [修复 P1-⑤] ──────────────────────────
        # 文档：使用 end_turn 时玩家实际持有的 block，而非累计增量
        # effective_block = min(end_block, intent_damage)
        expected_dmg = self.turn_tracker.expected_enemy_damage
        block_at_end_turn = float(end_player.get("block", 0) or 0)

        if expected_dmg > 0:
            effective_block = min(block_at_end_turn, expected_dmg)
            block_coverage = effective_block / expected_dmg
            reward += block_coverage * self.block_coverage_reward  # 完全覆盖 +1.0

            # 超额格挡轻微惩罚（避免无脑堆格挡）
            excess_block = max(block_at_end_turn - expected_dmg, 0.0)
            reward -= min(excess_block / 30.0, self.excess_block_penalty_cap)
        # 敌人不攻击：格挡价值低，不给奖励也不惩罚

        # ── B3: 能量利用率 [修复 P1-⑥] ──────────────────────────
        # 文档：waste_ratio = wasted_energy / max_energy（能量比，非手牌比）
        wasted_energy = float(end_combat.get("energy", 0) or 0)
        max_energy = max(float(end_combat.get("max_energy", 3) or 3), 1.0)
        hand_at_end = end_combat.get("hand") or []
        playable = [
            c for c in hand_at_end
            if isinstance(c.get("cost"), int) and (c.get("cost", 0) or 0) <= wasted_energy
        ]

        if playable and wasted_energy > 0:
            # 有牌可打却浪费能量：按能量比例惩罚
            waste_ratio = wasted_energy / max_energy
            reward -= waste_ratio * self.energy_waste_penalty  # 最多 -0.5
        # 无牌可打但有剩余能量：不惩罚（已尽力）

        return reward

    def _layer_b4_hp_loss(self) -> float:
        """
        B4: HP损失惩罚 [修复 P0-③]
        文档要求：hp_lost = end_turn_snapshot.player.hp - post_enemy_snapshot.player.hp
        即"玩家回合结束时 HP" 和"敌人行动后 HP"的差值。
        这才是真实被打掉的血量，而非 prev→new 的差分。
        """
        end_snap = self.turn_tracker.end_turn_snapshot
        post_snap = self.turn_tracker.post_enemy_snapshot
        if end_snap is None or post_snap is None:
            return 0.0

        end_hp  = _player_hp(end_snap)
        post_hp = _player_hp(post_snap)
        hp_lost = max(end_hp - post_hp, 0.0)

        if hp_lost <= 0:
            return 0.0

        max_hp = _player_max_hp(end_snap)
        hp_lost_ratio = hp_lost / max_hp

        # 动态惩罚：当前HP越少，每点损失惩罚越大（濒死时惩罚最高×2）
        current_hp_ratio = post_hp / max_hp
        urgency_mul = 1.0 + max(0.5 - current_hp_ratio, 0.0) * 2.0
        urgency_mul = min(urgency_mul, self.hp_loss_urgency_max_mul)

        penalty = hp_lost_ratio * self.hp_loss_penalty * urgency_mul
        return -penalty

    def layer_b_turn_reward(
        self, prev_state: Dict, new_state: Dict, action_kind: str
    ) -> float:
        """
        公开接口保留（向后兼容），内部已拆分为 _layer_b_partial + _layer_b4_hp_loss。
        在 shape() 中不再直接调用此方法，此处保留供外部单元测试使用。
        """
        if action_kind != "end_turn":
            return 0.0
        partial = self._layer_b_partial(prev_state, new_state)
        # 注意：单独调用时 post_enemy 快照未就绪，B4 返回 0
        b4 = self._layer_b4_hp_loss()
        return partial + b4

    # ──────────────────────────────────────────────────────────────
    # Layer C：战斗级结算奖励
    # ──────────────────────────────────────────────────────────────

    def layer_c_combat_reward(self, new_state: Dict, is_victory: bool) -> float:
        """
        [修复 P0-②] 完全对齐文档的战斗结算公式：

        NORMAL  : base=3.0  + HP效率奖励(最高4.0)
        ELITE   : base=8.0  + HP效率奖励(最高4.0) + 精英低损额外(+3.0，损失<30%时)
        BOSS    : base=20.0 + HP效率奖励(最高4.0) + Boss额外(+10.0)

        HP效率奖励 = (1 - hp_lost_ratio) * hp_efficiency_max
        """
        if not is_victory:
            return 0.0

        enemy_type = self.combat_tracker.combat_enemy_type

        # C1: 按敌人类型给基础奖励
        type_bonus_map = {
            "NORMAL": self.normal_combat_bonus,   # 3.0
            "ELITE":  self.elite_combat_bonus,    # 8.0
            "BOSS":   self.boss_combat_bonus,     # 20.0
        }
        reward = type_bonus_map.get(enemy_type, self.normal_combat_bonus)

        # C2: HP效率奖励 [修复 P0-②]
        # 文档：(1 - hp_lost_ratio) * 4.0
        # hp_lost_ratio = 战斗期间损失的HP / max_hp
        max_hp = _player_max_hp(new_state)
        start_hp = self.combat_tracker.combat_start_hp_ratio * max_hp
        end_hp   = _player_hp(new_state)
        hp_lost  = max(start_hp - end_hp, 0.0)
        hp_lost_ratio = hp_lost / max_hp
        hp_efficiency_bonus = (1.0 - hp_lost_ratio) * self.hp_efficiency_max
        reward += hp_efficiency_bonus

        # C3: 精英战额外奖励（损失<30%HP）
        if enemy_type == "ELITE" and hp_lost_ratio < self.elite_clean_threshold:
            reward += self.elite_clean_bonus   # +3.0

        # C4: Boss额外奖励
        if enemy_type == "BOSS":
            reward += self.boss_extra_bonus    # +10.0

        return reward

    # ──────────────────────────────────────────────────────────────
    # Layer D：局外阶段奖励
    # ──────────────────────────────────────────────────────────────

    def layer_d_meta_reward(
        self, prev_state: Dict, new_state: Dict, action: Dict
    ) -> float:
        """
        局外阶段奖励，总权重由 layer_d_weight=0.3 统一缩放（META_WEIGHT）。
        注意：不在此方法内部乘权重，由 shape() 统一乘。

        [修复 P2-⑧] 补充 buy_card / buy_relic / buy_potion
        [修复 P2-⑨] 休息判断读 prev_state（行动前），而非 new_state
        """
        action_kind = str(action.get("action") or action.get("type") or "")
        reward = 0.0

        # D1: 选牌
        if action_kind == "choose_reward_card":
            reward += self.choose_card_meta_bonus    # +0.3
        elif action_kind == "skip_reward_cards":
            reward += 0.0  # 不惩罚也不奖励

        # D2: 营地 [修复 P2-⑨：HP判断改用 prev_state]
        elif action_kind == "choose_rest_option":
            idx = int(action.get("option_index", action.get("index", 0)) or 0)
            if idx == 1:  # 锻造升级
                reward += self.smith_bonus  # +0.5
            elif idx == 0:  # 休息回血
                # 使用行动前的 HP 比例判断（未回血前）
                prev_hp_ratio = _player_hp_ratio(prev_state)
                if prev_hp_ratio < self.rest_low_threshold:     # <35%
                    reward += self.rest_low_hp_bonus            # +1.0
                elif prev_hp_ratio < self.rest_mid_threshold:   # 35%-60%
                    reward += self.rest_mid_hp_bonus            # +0.3
                elif prev_hp_ratio > self.rest_high_threshold:  # >80%
                    reward -= self.rest_high_hp_penalty         # -0.5

        # D3: 商店购买 [修复 P2-⑧]
        elif action_kind in ("buy_card", "buy_relic", "buy_potion"):
            reward += self.buy_bonus    # +0.2

        elif action_kind == "proceed":
            reward += 0.0  # 离开商店，中性

        # D4: 地图路线
        elif action_kind == "choose_map_node":
            reward += 0.0  # 不给即时奖励，靠后续战斗结果的 GAE 回传

        # D5: 事件选择
        elif action_kind == "choose_event_option":
            reward += 0.0  # 结果随机，不给即时奖励

        # D6: 删牌
        elif action_kind in ("remove_card_at_shop", "remove_card", "scrap"):
            reward += self.remove_card_bonus    # +0.4

        return reward

    # ──────────────────────────────────────────────────────────────
    # Layer E：终局奖励
    # ──────────────────────────────────────────────────────────────

    def layer_e_terminal_reward(self, final_state: Dict, done: bool) -> float:
        if not done:
            return 0.0

        # 胜利
        if bool((final_state.get("game_over") or {}).get("victory", False)):
            return self.terminal_victory_bonus  # +100

        # 死亡
        floor = float(final_state.get("floor", 0) or 0)
        hp = _player_hp(final_state)
        hp_quality = (hp / _player_max_hp(final_state)) * self.terminal_hp_quality_weight

        return (
            -self.terminal_defeat_penalty           # -30
            + floor * self.terminal_floor_weight    # 每层 +1.5
            + hp_quality                            # 存活时 HP 质量加成（死亡局=0）
        )

    # ──────────────────────────────────────────────────────────────
    # LLM match 奖励（不变，保留原逻辑）
    # ──────────────────────────────────────────────────────────────

    def _compute_card_match_bonus(self, agent_card_index: Optional[int]) -> float:
        if self.llm_advisor is None or agent_card_index is None:
            return 0.0
        rec_idx, conf = self.llm_advisor.get_last_card_recommendation()
        if rec_idx == -99 or conf < self.confidence_threshold:
            return 0.0
        if agent_card_index == rec_idx:
            return self.card_match_bonus
        if rec_idx == -1 and agent_card_index == -1:
            return self.card_match_bonus * 0.5
        if conf >= self.confidence_threshold + 0.1:
            return -self.card_mismatch_penalty
        return 0.0

    def _compute_relic_match_bonus(self, agent_relic_index: Optional[int]) -> float:
        if self.llm_advisor is None or agent_relic_index is None:
            return 0.0
        rec_idx, conf = self.llm_advisor.get_last_relic_recommendation()
        if rec_idx == -99 or conf < self.confidence_threshold:
            return 0.0
        if agent_relic_index == rec_idx:
            return 1.0
        if conf >= self.confidence_threshold + 0.1:
            return -0.5
        return 0.0

    def _compute_map_match_bonus(self, agent_map_index: Optional[int]) -> float:
        if self.llm_advisor is None or agent_map_index is None:
            return 0.0
        rec_idx, conf, route_scores = self.llm_advisor.get_last_map_recommendation()
        if rec_idx == -99 or conf < self.confidence_threshold:
            return 0.0
        if route_scores and 0 <= agent_map_index < len(route_scores):
            agent_score = float(route_scores[agent_map_index])
            best_score  = max(float(x) for x in route_scores)
            if best_score <= 1e-6:
                return 0.0
            return max(min((agent_score / best_score) - 0.5, 1.0), -0.5)
        if agent_map_index == rec_idx:
            return 1.0
        if conf >= self.confidence_threshold + 0.1:
            return -0.4
        return 0.0

    def _compute_combat_opening_bonus(
        self,
        combat_step: Optional[int],
        agent_card_played: Optional[int],
    ) -> float:
        if self.llm_advisor is None or combat_step is None or agent_card_played is None:
            return 0.0
        if combat_step >= self.combat_bias_steps:
            return 0.0
        advice = self.llm_advisor.get_last_combat_opening()
        if not advice:
            return 0.0
        seq = advice.get("opening_card_sequence", [])
        if not isinstance(seq, list) or combat_step >= len(seq):
            return 0.0
        try:
            expected = int(seq[combat_step])
        except Exception:
            return 0.0
        return 0.5 if expected == agent_card_played else -0.2

    # ──────────────────────────────────────────────────────────────
    # 工具方法
    # ──────────────────────────────────────────────────────────────

    @staticmethod
    def _should_query_llm(screen: str) -> bool:
        return screen not in ("COMBAT", "NONE", "LOADING", "")

    @staticmethod
    def _is_new_player_turn(old_state: Optional[Dict], new_state: Dict) -> bool:
        """
        检测是否进入了新的玩家回合。
        判据：能量从低值恢复到最大值（敌人行动结束、新回合抽牌后）。
        """
        if old_state is None:
            return False
        old_combat = old_state.get("combat") or {}
        new_combat = new_state.get("combat") or {}
        old_energy = float(old_combat.get("energy", 0) or 0)
        new_energy = float(new_combat.get("energy", 0) or 0)
        max_energy = float(new_combat.get("max_energy", 3) or 3)
        # 能量从 < max 恢复到 max → 新回合
        return old_energy < max_energy and new_energy >= max_energy