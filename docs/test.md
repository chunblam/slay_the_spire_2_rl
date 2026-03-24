很好，你这个顺序是对的：**先纯 RL 跑通闭环**，再加 LLM 奖励塑形。下面给你一套可直接执行的测试调试步骤。

## 一、先做“纯 RL 基线”配置

在 `RL_slay the spire/ppo_default.yaml` 里确认：

- `llm.enabled: false`
- `env.host: "127.0.0.1"`
- `env.port: 18080`（与你 Mod 一致）
- `env.character_index: 0`（先固定角色，便于复现）
- `train.total_steps: 20000`（先小步验证）

建议先把步数临时降到 `5000~10000` 做冒烟。

---

## 二、启动前联通检查（必须）

1. 启动游戏并确保 Mod 已加载  
2. 浏览器访问：
   - `http://127.0.0.1:18080/health`
3. 终端执行：
   - `python test_connection.py --host 127.0.0.1 --port 18080`

预期：
- 返回连接成功
- 能看到 `phase`、楼层等信息

---

## 三、做“自动开局链路”单测（不训练）

先只跑 `env.reset()` 逻辑是否自动开新局（单人->标准->角色->开始）。

你可以直接运行训练脚本，但把总步数设很小（例如 200），观察启动日志。  
重点看是否能自动进入 `run` 而不是卡菜单。

预期：
- 不需要手点，能进战斗/地图
- `info["screen_type"]` 不应长期停在 `NONE`

---

## 四、纯 RL 训练冒烟（第一轮）

运行：
- `python train.py --config ppo_default.yaml`

观察 3 类输出：

1. **Episode 是否持续推进**
   - `Episode x | Reward ... | Floor ...`
2. **PPO update 是否正常**
   - `pg_loss/vf_loss/entropy` 有数值，不是 NaN
3. **死亡后是否自动重开**
   - 结束后能自动回菜单再开局继续训练

---

## 五、重点排查项（若出错）

### 1) 卡在 reset/开局
- 常见原因：`menu_*` 在当前画面不合法
- 处理：确认 Mod 端 `session/legal_actions` 返回是否包含 `menu_new_run/menu_confirm`

### 2) 训练中频繁 invalid_action
- 常见原因：动作 mask 和 legal_actions 不匹配
- 处理：打印每步 `screen_type + legal_actions + action_executed`

### 3) 一直不结束 episode
- 常见原因：done 判断没触发到 `GAME_OVER`
- 处理：检查 `sts2_env.py` 中 `screen_type` 映射与 `game_over` 字段是否稳定

### 4) reward 基本不变
- 常见原因：状态字段映射后 HP/楼层/金币没有读到
- 处理：打印 `prev_state/new_state` 的关键字段（hp/floor/gold）

---

## 六、你这阶段的“预期效果”

### 纯 RL（短期）
- 前几千步表现通常较差，奖励波动大
- 能稳定形成“可运行训练闭环”就是成功（比策略好坏更重要）
- 平均楼层短期内可能只在低层徘徊（正常）

### 纯 RL（跑通标志）
- 能连续执行多 episode
- 死亡后可自动重开，不需要人工干预
- 没有频繁崩溃/卡死/HTTP错误风暴
- loss 指标可训练（非 NaN，且有波动）

---

## 七、通过基线后再开 LLM 的顺序

1. 保持其它参数不变，仅把 `llm.enabled: true`
2. 先开低频调用（`call_interval_steps` 大一点，比如 15~20）
3. 观察：
   - 每步耗时是否显著增加
   - reward 是否更平滑
   - 平均楼层是否优于纯 RL 基线

---

如果你愿意，我下一步可以给你一份“**30 分钟最小验证清单**”（每 5 分钟一个检查点，看到什么现象算通过），你照着跑能很快判断当前是否可进入正式训练。