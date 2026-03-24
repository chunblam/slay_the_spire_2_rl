"""
scripts/train.py
"""

import argparse
import json
import os
import sys
from typing import Dict, Optional
from datetime import datetime

import torch
import yaml

_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)
_parent = os.path.dirname(_ROOT)
if os.path.basename(_ROOT) == "scripts":
    sys.path.insert(0, _parent)

from sts2_env import STS2Env
from ppo_agent import STS2PolicyNet, PPOAgent
from llm_advisor import LLMAdvisor, LLMBackend
from reward_shaper import RewardShaper
from rollout_buffer import RolloutBuffer


# ── 配置加载 ──────────────────────────────────────────────────────────────────

def load_config(path: str) -> Dict:
    with open(path, "r", encoding="utf-8-sig") as f:
        return yaml.safe_load(f)


def _progress_state_path(checkpoint_dir: str) -> str:
    return os.path.join(checkpoint_dir, "training_state.json")


def _load_progress_state(path: str) -> Optional[Dict]:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        return None
    return None


def _save_progress_state(path: str, state: Dict):
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _extract_progress_snapshot(
    total_steps: int,
    episode: int,
    best_episode_reward: float,
    latest_checkpoint: str,
) -> Dict:
    return {
        "total_steps": int(total_steps),
        "episode": int(episode),
        "best_episode_reward": float(best_episode_reward),
        "latest_checkpoint": latest_checkpoint,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }


# ── 组件构建 ──────────────────────────────────────────────────────────────────

def build_agent(cfg: Dict, device: str) -> PPOAgent:
    policy = STS2PolicyNet(
        num_actions=cfg["env"]["num_actions"],
        hidden_dim=cfg["model"]["hidden_dim"],
    )
    return PPOAgent(
        policy=policy,
        lr=cfg["train"]["lr"],
        clip_eps=cfg["train"]["clip_eps"],
        value_loss_coef=cfg["train"]["value_loss_coef"],
        entropy_coef=cfg["train"]["entropy_coef"],
        gamma=cfg["train"]["gamma"],
        gae_lambda=cfg["train"]["gae_lambda"],
        device=device,
    )


def build_llm_advisor(cfg: Dict) -> Optional[LLMAdvisor]:
    llm_cfg = cfg.get("llm", {})
    if not llm_cfg.get("enabled", False):
        print("ℹ️  LLM Advisor 已禁用")
        return None

    backend = LLMBackend(
        backend=llm_cfg.get("backend", "ollama"),
        model=llm_cfg.get("model", "qwen2.5:7b"),
        api_key=llm_cfg.get("api_key", ""),
    )
    advisor = LLMAdvisor(
        llm_backend=backend,
        knowledge_base_path=llm_cfg.get("knowledge_base_path", "data/knowledge_base.json"),
        call_interval_steps=llm_cfg.get("call_interval_steps", 10),
        card_shaping_confidence_threshold=llm_cfg.get("confidence_threshold", 0.55),
        combat_bias_steps=llm_cfg.get("combat_bias_steps", 3),
    )
    print(f"✅ LLM Advisor: {llm_cfg.get('backend')} / {llm_cfg.get('model')}")
    return advisor


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _extract_agent_card_index(executed_action: Dict, screen_type: str) -> Optional[int]:
    """
    从已执行动作里解析 agent 选的卡牌索引（仅 CARD_REWARD 屏幕）。
    返回 None 表示不是选牌动作，不触发 match bonus。

    STS2AIAgent API:
      - {"action": "choose_reward_card", "option_index": k}  k 为 0-based
      - {"action": "skip_reward_cards"}
    旧式封装（若日后在 env 里包一层）:
      - {"type": "choose_reward", "payload": {"skip": bool, "card_index": int}}
    """
    if screen_type != "CARD_REWARD":
        return None

    raw = executed_action.get("action", "")
    if raw == "skip_reward_cards":
        return -1
    if raw == "choose_reward_card":
        return int(executed_action.get("option_index", 0))

    if executed_action.get("type") == "choose_reward":
        payload = executed_action.get("payload", {})
        if payload.get("skip", False):
            return -1
        return int(payload.get("card_index", 0))

    return None


def _get_reward_cards_from_state(state: Dict) -> list:
    """从游戏状态里取出奖励卡列表（兼容 Raw API 的字段名差异）"""
    # Raw API 用 card_reward 或 reward，结构可能是 {"cards": [...]} 或直接是列表
    cr = state.get("card_reward") or state.get("reward") or {}
    if isinstance(cr, dict):
        return cr.get("cards", [])
    if isinstance(cr, list):
        return cr
    return []


def _get_map_options_from_state(state: Dict) -> list:
    m = state.get("map") or {}
    opts = m.get("next_options")
    return list(opts) if isinstance(opts, list) else []


def _get_relic_options_from_state(state: Dict) -> list:
    chest = state.get("chest") or {}
    relics = chest.get("relic_options")
    if isinstance(relics, list) and relics:
        return relics

    reward = state.get("reward") or {}
    rewards = reward.get("rewards")
    if isinstance(rewards, list):
        out = []
        for r in rewards:
            if not isinstance(r, dict):
                continue
            name = str(r.get("name", "")).lower()
            kind = str(r.get("type", "")).lower()
            if "relic" in name or "relic" in kind:
                out.append(r)
        if out:
            return out
    return []


def _extract_agent_relic_index(executed_action: Dict) -> Optional[int]:
    action = str(executed_action.get("action", ""))
    if action == "choose_treasure_relic":
        return int(executed_action.get("option_index", 0))
    return None


def _extract_agent_map_index(executed_action: Dict) -> Optional[int]:
    action = str(executed_action.get("action", ""))
    if action == "choose_map_node":
        return int(executed_action.get("option_index", 0))
    return None


def _extract_combat_card_played(executed_action: Dict) -> Optional[int]:
    action = str(executed_action.get("action", ""))
    if action == "play_card":
        return int(executed_action.get("card_index", 0))
    return None


class RunLogger:
    """
    运行期分模块日志：
    logs/<YYYYmmdd_HHMMSS>/
      - module_agent_decision.log
      - module_env_step_state.log
      - module_reward_shaping.log
      - module_ppo_update.log
      - module_episode_summary.log
      - module_error_recovery.log
      - run_config_snapshot.json
    """

    def __init__(self, cfg: Dict):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_dir = os.path.join("logs", ts)
        os.makedirs(self.run_dir, exist_ok=True)
        self.paths = {
            "agent_decision": os.path.join(self.run_dir, "module_agent_decision.log"),
            "env_step_state": os.path.join(self.run_dir, "module_env_step_state.log"),
            "reward_shaping": os.path.join(self.run_dir, "module_reward_shaping.log"),
            "ppo_update": os.path.join(self.run_dir, "module_ppo_update.log"),
            "episode_summary": os.path.join(self.run_dir, "module_episode_summary.log"),
            "error_recovery": os.path.join(self.run_dir, "module_error_recovery.log"),
        }
        with open(os.path.join(self.run_dir, "run_config_snapshot.json"), "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        print(f"🗂️  本次运行日志目录: {self.run_dir}")

    def log(self, key: str, msg: str):
        path = self.paths.get(key)
        if not path:
            return
        stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"[{stamp}] {msg}\n")


# ── 主训练循环 ────────────────────────────────────────────────────────────────

def train(cfg: Dict):
    device = cfg.get("device", "cpu")
    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"🚀 开始训练 | 设备: {device}")
    run_logger = RunLogger(cfg)

    env = STS2Env(
        host=cfg["env"].get("host", "localhost"),
        port=cfg["env"].get("port", 18080),
        character_index=cfg["env"].get("character_index", 0),
        startup_debug=cfg["env"].get("startup_debug", False),
        action_poll_interval=cfg["env"].get("action_poll_interval", 0.5),
        action_min_interval=cfg["env"].get("action_min_interval", 0.5),
        post_action_settle=cfg["env"].get("post_action_settle", 0.5),
        action_retry_count=cfg["env"].get("action_retry_count", 1),
        render_mode="human" if cfg.get("render") else None,
    )
    agent        = build_agent(cfg, device)
    llm_advisor  = build_llm_advisor(cfg)
    reward_shaper = RewardShaper(
        llm_advisor=llm_advisor,
        llm_weight=cfg.get("reward", {}).get("llm_weight", 0.3),
        rule_weight=cfg.get("reward", {}).get("rule_weight", 0.5),
        card_weight=cfg.get("reward", {}).get("card_weight", 0.4),
        card_match_bonus=cfg.get("reward", {}).get("card_match_bonus", 1.0),
        card_mismatch_penalty=cfg.get("reward", {}).get("card_mismatch_penalty", 0.5),
        confidence_threshold=cfg.get("llm", {}).get("confidence_threshold", 0.55),
        relic_choice_weight=cfg.get("reward", {}).get("relic_choice_weight", 0.25),
        map_route_weight=cfg.get("reward", {}).get("map_route_weight", 0.25),
        combat_opening_weight=cfg.get("reward", {}).get("combat_opening_weight", 0.2),
        combat_bias_steps=cfg.get("llm", {}).get("combat_bias_steps", 3),
    )
    buffer = RolloutBuffer(buffer_size=cfg["train"]["buffer_size"])

    checkpoint_dir = cfg.get("checkpoint_dir", "checkpoints")
    os.makedirs(checkpoint_dir, exist_ok=True)
    train_cfg = cfg.get("train", {})
    resume_on_restart = bool(train_cfg.get("resume_on_restart", True))
    save_latest_per_update = bool(train_cfg.get("save_latest_per_update", True))
    progress_path = _progress_state_path(checkpoint_dir)
    latest_ckpt_path = os.path.join(checkpoint_dir, "latest_model.pt")

    resume_path = cfg.get("resume")
    state_payload = _load_progress_state(progress_path) if resume_on_restart else None
    auto_resume_path = None
    if state_payload:
        candidate = str(state_payload.get("latest_checkpoint", "")).strip()
        if candidate and os.path.exists(candidate):
            auto_resume_path = candidate
        elif os.path.exists(latest_ckpt_path):
            auto_resume_path = latest_ckpt_path

    effective_resume = resume_path if (resume_path and os.path.exists(resume_path)) else auto_resume_path
    if effective_resume:
        agent.load(effective_resume)
        print(f"📂 已加载断点: {effective_resume}")

    total_steps = int(state_payload.get("total_steps", 0)) if state_payload else 0
    episode = int(state_payload.get("episode", 0)) if state_payload else 0
    best_episode_reward = float(state_payload.get("best_episode_reward", float("-inf"))) if state_payload else float("-inf")
    if state_payload and resume_on_restart:
        print(f"🔁 已恢复训练进度: total_steps={total_steps}, episode={episode}, best={best_episode_reward:.3f}")

    consecutive_env_errors = 0
    max_consecutive_env_errors = 10

    print("⏳ 等待游戏就绪（确保 STS2 + STS2AIAgent Mod 已运行）...")
    obs, info = env.reset()
    run_logger.log("env_step_state", f"reset: screen={info.get('screen_type')} floor={info.get('floor')} hp={info.get('hp')}/{info.get('max_hp')}")
    prev_screen = ""
    combat_step_counter = 0
    llm_card_triggered = False

    while total_steps < cfg["train"]["total_steps"]:

        # ── 收集 Rollout ──────────────────────────────────────────────────
        buffer.reset()
        episode_rewards       = []
        current_ep_reward     = 0.0

        while not buffer.is_full():
            screen_type = info.get("screen_type", "")
            raw_state   = info.get("raw_state", {})
            run_logger.log(
                "env_step_state",
                f"pre_step total_steps={total_steps} buffer_size={len(buffer)} screen={screen_type} floor={info.get('floor')} gold={info.get('gold')}",
            )

            # ── ① LLM战略触发节点（仅 llm.enabled=true 时生效）──────────────
            if screen_type == "CARD_REWARD" and not llm_card_triggered and llm_advisor is not None:
                reward_cards = _get_reward_cards_from_state(raw_state)
                if reward_cards:
                    llm_advisor.evaluate_card_reward(raw_state, reward_cards)
                llm_card_triggered = True
            elif screen_type != "CARD_REWARD":
                llm_card_triggered = False

            if screen_type == "MAP" and prev_screen != "MAP" and llm_advisor is not None:
                route_options = _get_map_options_from_state(raw_state)
                if route_options:
                    llm_advisor.evaluate_map_route(raw_state, route_options)

            if screen_type in ("CHEST", "REWARD") and prev_screen not in ("CHEST", "REWARD") and llm_advisor is not None:
                relic_options = _get_relic_options_from_state(raw_state)
                if relic_options:
                    llm_advisor.evaluate_relic_choice(raw_state, relic_options)

            if screen_type == "COMBAT" and prev_screen != "COMBAT":
                combat_step_counter = 0
                if llm_advisor is not None:
                    llm_advisor.evaluate_combat_opening(raw_state)

            # ── ② 构建 obs tensor ─────────────────────────────────────────
            obs_tensor = {
                k: torch.tensor(
                    v,
                    dtype=torch.float32 if k != "screen_type" else torch.long,
                ).unsqueeze(0).to(device)
                for k, v in obs.items()
            }

            # ── ③ 获取动作 mask ───────────────────────────────────────────
            action_mask_list   = env.action_handler.get_valid_action_mask(raw_state)
            action_mask_tensor = torch.tensor([action_mask_list], dtype=torch.bool).to(device)

            # ── ④ Agent 决策（RL 完全自主，不受 LLM 直接干预）─────────────
            with torch.no_grad():
                action, log_prob, value = agent.policy.get_action(
                    obs_tensor, action_mask_tensor
                )
            action_id = action.item()
            run_logger.log(
                "agent_decision",
                f"step={total_steps} screen={screen_type} action_id={action_id} "
                f"log_prob={log_prob.item():.4f} value={value.item():.4f} "
                f"valid_actions={sum(1 for m in action_mask_list if m)}",
            )

            # ── ⑤ 执行动作 ───────────────────────────────────────────────
            try:
                next_obs, base_reward, done, truncated, next_info = env.step(action_id)
                consecutive_env_errors = 0
            except RuntimeError as e:
                # 方案2：环境错误容错恢复，避免单次非法动作导致整轮训练崩溃
                msg = str(e)
                recoverable = (
                    "No unlocked event options available" in msg
                    or "status" in msg.lower()
                    or "Invalid" in msg
                )
                if recoverable:
                    run_logger.log("error_recovery", f"recoverable_runtime_error: {msg}")
                    consecutive_env_errors += 1
                    print(
                        f"⚠️  env.step 可恢复错误({consecutive_env_errors}/{max_consecutive_env_errors}): {msg}"
                    )
                    if consecutive_env_errors >= max_consecutive_env_errors:
                        raise RuntimeError(
                            f"连续环境错误达到上限: {msg}"
                        ) from e
                    obs, info = env.reset()
                    prev_screen = ""
                    combat_step_counter = 0
                    llm_card_triggered = False
                    continue
                raise
            executed_action = next_info.get("action_executed", {})
            run_logger.log(
                "env_step_state",
                f"post_step action={executed_action} next_screen={next_info.get('screen_type')} floor={next_info.get('floor')}",
            )
            manual_intervention = bool(next_info.get("manual_intervention", False))
            if manual_intervention:
                reason = next_info.get("manual_intervention_reason", "unknown")
                run_logger.log("error_recovery", f"manual_intervention: reason={reason}")
                print(
                    f"🧑‍🔧 检测到人工介入步骤（{reason}），本步不计入 buffer/奖励，继续后续状态。"
                )
                obs = next_obs
                info = next_info
                continue

            # ── ⑥ 解析 agent 是否做了选牌动作 ────────────────────────────
            agent_card_index = _extract_agent_card_index(executed_action, screen_type)
            agent_relic_index = _extract_agent_relic_index(executed_action)
            agent_map_index = _extract_agent_map_index(executed_action)
            combat_card_played = _extract_combat_card_played(executed_action)

            # ── ⑦ 奖励塑形（含 match bonus）──────────────────────────────
            shaped_reward = reward_shaper.shape(
                base_reward=base_reward,
                prev_state=raw_state,
                new_state=next_info.get("raw_state", {}),
                action=executed_action,
                done=done,
                agent_card_index=agent_card_index,   # ← 新增传参
                agent_relic_index=agent_relic_index,
                agent_map_index=agent_map_index,
                combat_step=combat_step_counter if screen_type == "COMBAT" else None,
                agent_card_played=combat_card_played,
            )
            run_logger.log(
                "reward_shaping",
                f"step={total_steps} action={executed_action.get('action', executed_action.get('type'))} "
                f"base={base_reward:.4f} shaped={shaped_reward:.4f} done={done} truncated={truncated}",
            )

            # ── ⑧ 写入 buffer ─────────────────────────────────────────────
            buffer.add(
                obs=obs,
                action=action_id,
                log_prob=log_prob.item(),
                reward=shaped_reward,
                done=done or truncated,
                value=value.item(),
                action_mask=action_mask_list,
            )

            current_ep_reward += shaped_reward
            total_steps += 1
            obs                = next_obs
            info               = next_info
            if screen_type == "COMBAT":
                combat_step_counter += 1
            prev_screen = screen_type

            if resume_on_restart:
                snapshot = _extract_progress_snapshot(
                    total_steps=total_steps,
                    episode=episode,
                    best_episode_reward=best_episode_reward,
                    latest_checkpoint=effective_resume or "",
                )
                _save_progress_state(progress_path, snapshot)

            if done or truncated:
                episode += 1
                episode_rewards.append(current_ep_reward)
                run_logger.log(
                    "episode_summary",
                    f"episode={episode} reward={episode_rewards[-1]:.4f} floor={info.get('floor', 0)} hp={info.get('hp', 0)} total_steps={total_steps}",
                )
                current_ep_reward = 0.0
                floor_r = info.get("floor", 0)
                hp_r    = info.get("hp", 0)
                print(
                    f"  Episode {episode:4d} | "
                    f"Reward: {episode_rewards[-1]:8.2f} | "
                    f"Floor: {floor_r:2d} | "
                    f"HP: {hp_r:3d} | "
                    f"Steps: {total_steps}"
                )
                obs, info = env.reset()
                prev_screen = ""
                combat_step_counter = 0
                llm_card_triggered = False

        # ── GAE ───────────────────────────────────────────────────────────
        obs_t = {
            k: torch.tensor(
                v, dtype=torch.float32 if k != "screen_type" else torch.long
            ).unsqueeze(0).to(device)
            for k, v in obs.items()
        }
        with torch.no_grad():
            _, last_value = agent.policy.forward(obs_t)
        last_value = last_value.item()

        advantages, returns = agent.compute_gae(
            rewards=buffer.rewards,
            values=buffer.values,
            dones=buffer.dones,
            last_value=last_value,
        )
        buffer.set_gae_results(advantages, returns)

        # ── PPO 更新 ──────────────────────────────────────────────────────
        metrics = agent.update(
            buffer,
            n_epochs=cfg["train"]["n_epochs"],
            batch_size=cfg["train"]["batch_size"],
        )
        run_logger.log(
            "ppo_update",
            f"update_at_steps={total_steps} batch_size={cfg['train']['batch_size']} "
            f"buffer_size={cfg['train']['buffer_size']} pg_loss={metrics['pg_loss']:.6f} "
            f"vf_loss={metrics['vf_loss']:.6f} entropy={metrics['entropy']:.6f}",
        )
        if save_latest_per_update:
            agent.save(latest_ckpt_path)
            effective_resume = latest_ckpt_path
            run_logger.log("ppo_update", f"latest_model_saved: {latest_ckpt_path}")
            if resume_on_restart:
                snapshot = _extract_progress_snapshot(
                    total_steps=total_steps,
                    episode=episode,
                    best_episode_reward=best_episode_reward,
                    latest_checkpoint=effective_resume,
                )
                _save_progress_state(progress_path, snapshot)

        # ── 日志 & 存档 ───────────────────────────────────────────────────
        if episode_rewards:
            avg = sum(episode_rewards) / len(episode_rewards)
            print(
                f"\n📊 step {total_steps:7d} | avg_r: {avg:.3f} | "
                f"pg: {metrics['pg_loss']:.4f} | "
                f"vf: {metrics['vf_loss']:.4f} | "
                f"ent: {metrics['entropy']:.4f}\n"
            )
            if avg > best_episode_reward:
                best_episode_reward = avg
                best_path = os.path.join(checkpoint_dir, "best_model.pt")
                agent.save(best_path)
                print(f"  💾 最优模型: {best_path} (avg={avg:.3f})")
                if resume_on_restart:
                    snapshot = _extract_progress_snapshot(
                        total_steps=total_steps,
                        episode=episode,
                        best_episode_reward=best_episode_reward,
                        latest_checkpoint=effective_resume or "",
                    )
                    _save_progress_state(progress_path, snapshot)

        if total_steps % cfg["train"].get("save_interval", 50000) == 0:
            ckpt = os.path.join(checkpoint_dir, f"checkpoint_{total_steps}.pt")
            agent.save(ckpt)
            print(f"  💾 存档: {ckpt}")
            run_logger.log("ppo_update", f"checkpoint_saved: {ckpt}")
            effective_resume = ckpt
            if resume_on_restart:
                snapshot = _extract_progress_snapshot(
                    total_steps=total_steps,
                    episode=episode,
                    best_episode_reward=best_episode_reward,
                    latest_checkpoint=effective_resume,
                )
                _save_progress_state(progress_path, snapshot)

    print(f"\n🎉 训练完成! 总步数: {total_steps}")
    if resume_on_restart:
        snapshot = _extract_progress_snapshot(
            total_steps=total_steps,
            episode=episode,
            best_episode_reward=best_episode_reward,
            latest_checkpoint=effective_resume or "",
        )
        _save_progress_state(progress_path, snapshot)
    env.close()


# ── 入口 ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="STS2 RL Agent 训练")
    parser.add_argument(
        "--config", type=str, default="ppo_default.yaml",
        help="YAML 配置路径",
    )
    parser.add_argument("--render",  action="store_true")
    parser.add_argument("--resume",  type=str, default=None)
    args = parser.parse_args()

    cfg = load_config(args.config)
    if args.render:
        cfg["render"] = True
    if args.resume:
        cfg["resume"] = args.resume

    train(cfg)