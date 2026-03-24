# 纯 RL（无 LLM）小步数测试与调试指南

本文用于当前项目的「先跑通 PPO + 环境链路」阶段。目标是：**在不开启 LLM 的情况下完成一次可复现的小步数训练冒烟测试**，验证数据流、动作流、奖励流都正常。

---

## 1. 测试目标与预期效果

### 1.1 目标

- 验证 `train.py` 能稳定跑起来，不因接口或状态解析报错中断。
- 验证 RL 主链路（状态编码 → 动作采样 → Session API 执行动作 → 奖励塑形 → PPO 更新）完整。
- 验证 `llm.enabled: false` 时，LLM 相关逻辑不会干扰训练。

### 1.2 小步数阶段的合理预期

在 `2e4 ~ 5e4` 步范围内，通常属于「冒烟 + 初步探索」而不是「有效策略收敛」，预期现象如下：

- 能看到连续的 episode 输出与 update 输出，不频繁崩溃。
- 回报波动大是正常的，平均楼层不一定明显提升。
- 胜率通常接近 0 或非常低（正常）。
- 行为较随机，经常出现低质量路线/出牌选择（正常）。

不应出现的现象（出现即需排查）：

- 连续 `reward=0` 或长期无 episode 结束。
- `pg_loss/vf_loss` 出现 `nan`。
- 频繁 HTTP 错误（尤其 409、连接超时）。

---

## 2. 测试前配置（无 LLM）

编辑 `ppo_default.yaml`：

- `llm.enabled: false`
- `train.total_steps: 20000`（建议先 2 万，跑通后再到 5 万）
- `env.port` 与 Mod 实际端口一致（当前默认 18080）
- `env.action_poll_interval` / `env.action_min_interval` / `env.post_action_settle` 按观感调节（建议先 0.8 / 0.5 / 0.5）

注意：当前 `ppo_default.yaml` 的 `train.total_steps` 需要是合法数字，不能留空。

---

## 3. 执行步骤（建议顺序）

1. 激活环境并进入项目根目录。
2. 启动游戏，确保 STS2AIAgent Mod 已启用。
3. 先跑连接测试：
   - `python test_connection.py`
4. 连接成功后，开始小步数训练：
   - `python train.py --config ppo_default.yaml`
5. 观察终端日志与游戏画面，确认以下两类日志持续出现：
   - Episode 行（Reward/Floor/HP/Steps）
   - Update 行（avg_reward/pg_loss/vf_loss/entropy）

---

## 4. 游戏端你会看到什么（无 LLM）

在 `llm.enabled: false` 下，游戏端由 RL agent 全自动操作：

- 战斗：自动打牌、用药、结束回合。
- 奖励牌：自动选择或跳过（仅 RL 策略驱动）。
- 地图：自动选路。
- 营地：自动选休息/锻造。
- 事件/商店：自动做选择。

你需要做的人工操作：

- 只需确保游戏在一个可操作 run 内（不要停在主菜单）。
- 不要手动抢操作；让 agent 连续控制。
- 若卡在非标准 UI（弹窗/过场），手动点回可操作界面后再继续。

---

## 5. 本方案调用模块与顺序（无 LLM）

下面是一次训练 step 的实际调用链（`llm.enabled: false`）：

1. **配置加载**
   - `train.py::load_config()`

2. **组件初始化**
   - `STS2Env`（环境、HTTP 接口）
   - `PPOAgent` / `STS2PolicyNet`（策略与优化器）
   - `build_llm_advisor()` 返回 `None`
   - `RewardShaper(llm_advisor=None, ...)`
   - `RolloutBuffer`

3. **环境 reset**
   - `STS2Env.reset()`
   - `_ensure_run_ready()` 自动开局并进入 run 阶段
   - `StateEncoder.encode()` 产出 obs

4. **每个交互 step**
   - `StateEncoder` 输出的 `obs` 转 tensor
   - `STS2ActionSpace.get_valid_action_mask(raw_state)` 计算合法动作 mask
   - `policy.get_action(obs, mask)` 采样离散动作
   - `STS2Env.step(action_id)`
    - `STS2ActionSpace.decode()` 将动作 ID 映射为 Session API JSON（根字段 `action`）
    - `_execute_action_with_recovery()` 通过 `POST /api/v1/session/action` 执行动作并获取新状态
     - `_compute_reward()` 计算基础奖励
     - `_build_info()` 返回 `raw_state/screen/floor/hp/...`
   - `RewardShaper.shape(...)`
     - 规则奖励始终启用
     - 因 `llm_advisor is None`，不执行路线奖励、不执行选牌匹配奖励
   - `RolloutBuffer.add(...)` 存入本步数据

5. **rollout 满后更新**
   - `PPOAgent.compute_gae()` 计算优势与回报
   - `RolloutBuffer.set_gae_results(...)`
   - `PPOAgent.update(...)` 执行 PPO 多 epoch 更新
   - 按 `save_interval` 保存 checkpoint

---

## 6. 为什么这是正确的「纯 RL 基线」

`llm.enabled: false` 时：

- `build_llm_advisor()` 直接返回 `None`。
- `train.py` 中 CARD_REWARD 的 LLM 推荐分支不会触发。
- `RewardShaper` 只保留 `base_reward + rule_weight * rule_bonus`。

因此这条链路就是你要的「无 LLM 纯 RL（含规则塑形）基线」。

---

## 7. 调试重点与判定标准

### 7.1 判定“跑通”的最低标准

- 能连续跑到至少 3 次 PPO update。
- 无 HTTP 异常中断、无 NaN。
- 游戏端可见 agent 连续操作多个屏幕（战斗/地图/奖励）。

### 7.2 常见问题优先级

1. **连接问题**：先看 `test_connection.py` 是否稳定。
2. **动作门控问题**：409 常见于动作不在 `legal_actions`、或 `option_index/target_index` 参数不合法。
3. **状态问题**：游戏停在不可操作界面会导致 reset/step 卡住。
4. **数值问题**：若 NaN，先降学习率（例如 `1e-4`）。

---

## 8. 通过后下一步

当纯 RL 冒烟测试通过后，再开启 `llm.enabled: true` 对比实验（A/B）才有意义。建议固定其他参数，单独观察 LLM 开关对：

- 平均楼层
- 平均奖励
- 首幕胜率

的影响。

