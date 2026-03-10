# STS2 AI Agent 当前开发路线图

更新时间：`2026-03-10`

---

## 总体进度

| 阶段 | 描述 | 状态 |
| --- | --- | --- |
| Phase 0A | 环境搭建 | 已完成 |
| Phase 0B | 逆向侦察 | 已完成 |
| Phase 1A | 协议冻结 | 已完成 |
| Phase 1B | Mod 骨架 + `/health` | 已完成 |
| Phase 1C | 最小纵切 | 已完成 |
| Phase 2 | 战斗状态提取 | 已完成 |
| Phase 3 | 战斗动作执行 | 已完成 |
| Phase 4A | 地图 / 奖励 / 宝箱 | 代码已完成，部分已实机验证 |
| Phase 4B | 事件 / 休息点 | 未开始 |
| Phase 4C | 商店 | 未开始 |
| Phase 5 | MCP 完整化 | 基础已完成，随 4B/4C 同步扩展 |
| Phase 6 | 集成与回归 | 未开始 |

---

## 当前能力盘点

### HTTP API

| 端点 | 状态 |
| --- | --- |
| `GET /health` | 已验证 |
| `GET /state` | 已验证 |
| `GET /actions/available` | 已验证 |
| `POST /action` | 已验证 |

### 已实现动作

| 动作 | 实机状态 |
| --- | --- |
| `end_turn` | 已验证 |
| `play_card` | 已验证 |
| `choose_map_node` | 已验证 |
| `proceed` | 已验证 |
| `claim_reward` | 已验证 |
| `choose_reward_card` | 已验证 |
| `collect_rewards_and_proceed` | 已验证 |
| `skip_reward_cards` | 待验证 |
| `select_deck_card` | 待验证 |

### 已实现状态字段

| 字段 | 状态 |
| --- | --- |
| `combat.player` / `combat.hand` / `combat.enemies` | 已实现并验证 |
| `run.deck` / `run.relics` / `run.potions` / `run.gold` | 已实现，部分已验证 |
| `map.current_node` / `map.available_nodes` | 已验证 |
| `map.rows` / `map.cols` / `map.starting_node` / `map.boss_node` / `map.second_boss_node` / `map.nodes` | 已实现，待实机验证 |
| `reward.*` / `selection.*` | 已实现，部分已验证 |
| `event` | 仍为 `null` |
| `shop` | 仍为 `null` |
| `rest` | 仍为 `null` |
| `game_over` | 仍为 `null` |

### MCP 工具

当前 MCP 已注册并可用的基础工具：

- `health_check`
- `get_game_state`
- `get_available_actions`
- `end_turn`
- `play_card`
- `choose_map_node`
- `claim_reward`
- `choose_reward_card`
- `skip_reward_cards`
- `select_deck_card`
- `collect_rewards_and_proceed`
- `proceed`

---

## 分工原则

### 总原则

- **禁止同时修改同一个文件。**
- **Codex 负责 C# Mod 实现与实机联调。**
- **Claude 负责 Python MCP、协议文档、代码审查。**
- 如需跨边界改动，先在本文件更新“任务归属”，再开始改。

### 文件边界

| 文件 / 目录 | 归属 | 说明 |
| --- | --- | --- |
| `STS2AIAgent/**/*.cs` | Codex | C# Mod 全部实现 |
| `mcp_server/src/sts2_mcp/*.py` | Claude | Python MCP 客户端与 FastMCP 工具 |
| `mcp_server/README.md` | Claude | MCP 使用说明 |
| `docs/api.md` | Claude | 对外协议文档，以 C# 已落地实现为准同步 |
| `docs/phase-1c-status.md` | Codex | 实机验证记录与当前能力说明 |
| `docs/roadmap-current.md` | 共用，但改动前先声明归属 | 只写进度、分工、阻塞 |

### 禁止越界

- Codex 不主动改 `mcp_server/src/sts2_mcp/*.py`，除非用户明确要求或 Claude 尚未接手且任务被阻塞。
- Claude 不主动改 `STS2AIAgent/**/*.cs`，除非用户明确要求或 Codex 明确移交。
- 双方都不要在未同步的情况下重写同一份文档。

---

## 当前明确分工

### Codex 负责

#### T-C1: Phase 4A 收尾与回归

- 实机验证 `map.nodes`
- 实机验证 `skip_reward_cards`
- 实机验证 `select_deck_card`
- 持续更新 `docs/phase-1c-status.md`

涉及文件：

- `STS2AIAgent/Game/GameStateService.cs`
- `STS2AIAgent/Game/GameActionService.cs`
- `docs/phase-1c-status.md`

#### T-C2: Phase 4B 事件系统

- 实现 `EventPayload`
- 实现 `choose_event_option`
- 接入 `BuildStatePayload()`
- 接入 `BuildAvailableActionNames()`
- 接入 `GameActionService.ExecuteAsync()`

涉及文件：

- `STS2AIAgent/Game/GameStateService.cs`
- `STS2AIAgent/Game/GameActionService.cs`

#### T-C3: Phase 4B 休息点系统

- 实现 `RestPayload`
- 实现 `rest_site_action`
- 复用或扩展 `select_deck_card` 覆盖升级牌场景

涉及文件：

- `STS2AIAgent/Game/GameStateService.cs`
- `STS2AIAgent/Game/GameActionService.cs`

#### T-C4: Phase 4C 商店系统

- 实现 `ShopPayload`
- 实现 `buy_card`
- 实现 `buy_relic`
- 实现 `buy_potion`
- 实现 `remove_card_at_shop`

涉及文件：

- `STS2AIAgent/Game/GameStateService.cs`
- `STS2AIAgent/Game/GameActionService.cs`

#### T-C5: Game Over 状态

- 实现 `GameOverPayload`
- 接入 `BuildStatePayload()`

涉及文件：

- `STS2AIAgent/Game/GameStateService.cs`

#### T-C6: 低优先级重构

- 拆分 `GameStateService.cs` 内部 Payload 类型
- 仅做文件组织，不改行为

涉及文件：

- `STS2AIAgent/Game/GameStateService.cs`
- 新增 `STS2AIAgent/Game/Payloads/*.cs` 或类似目录

### Claude 负责

#### T-M1: 事件系统 MCP

前置条件：T-C2 完成

- 在 `client.py` 新增 `choose_event_option(option_index)`
- 在 `server.py` 注册 `choose_event_option`
- 补 docstring

#### T-M2: 休息点系统 MCP

前置条件：T-C3 完成

- 在 `client.py` 新增 `rest_site_action(action, option_index?)`
- 在 `server.py` 注册对应 MCP 工具
- 补 docstring

#### T-M3: 商店系统 MCP

前置条件：T-C4 完成

- 在 `client.py` 新增商店相关方法
- 在 `server.py` 注册对应 MCP 工具
- 补 docstring

#### T-M4: 协议文档同步

- 随 T-C2 / T-C3 / T-C4 / T-C5 完成后更新 `docs/api.md`
- 为新 Payload 与新动作补示例
- 保证文档与 C# 已实现行为一致

#### T-M5: 代码审查

- 每完成一个 T-C 任务就做一次 review
- 重点检查：
  - 错误处理
  - 线程安全
  - 命名与结构一致性
  - 是否破坏既有动作语义

---

## 执行顺序

1. Codex 先完成 T-C1，把 `map.nodes`、`skip_reward_cards`、`select_deck_card` 实机收口。
2. Codex 进入 T-C2 事件系统。
3. T-C2 完成后，Claude 开始 T-M1，同时 Codex 进入 T-C3。
4. T-C3 完成后，Claude 开始 T-M2，同时 Codex 进入 T-C4。
5. T-C4 完成后，Claude 开始 T-M3，同时 Codex 进入 T-C5。
6. 文档同步 T-M4 和审查 T-M5 持续穿插，不阻塞主线。
7. 所有 4B / 4C 完成后，再进入 Phase 6 集成与回归。

---

## 并行规则

- `T-C2`、`T-C3`、`T-C4` 都会改 `GameStateService.cs` 和 `GameActionService.cs`，所以 **只能串行**。
- Claude 只能在对应 C# 功能落地后，再开始 MCP 封装。
- `docs/api.md` 由 Claude 维护，但必须以 C# 已提交实现为准，不先写未来接口。
- `docs/phase-1c-status.md` 由 Codex 维护，只记录已经实现或已经验证的内容。

---

## 交接协议

### Codex -> Claude

每完成一个 C# 功能，Codex 交接时必须给出：

- 新增/变更动作名
- `GET /state` 新增字段
- 成功路径
- 失败路径
- 是否已经实机验证
- 涉及文件

### Claude -> Codex

每完成一个 MCP 封装或审查，Claude 回传：

- 新增工具名
- 参数定义
- 与 HTTP 字段映射关系
- review 发现的问题清单

---

## 当前阻塞与风险

1. `GameStateService.cs` 是高冲突文件，后续 4B/4C 必须串行推进。
2. Windows 下不能热替换已加载的 Mod DLL，所有实机验证都依赖“关游戏 -> 安装 -> 重开”。
3. STS2 更新后可能导致逆向入口失效，事件、商店、休息点都要优先找稳定入口。
4. `map.nodes` 目前只提供图结构和运行时状态，不提供内建评分；路线规划逻辑必须在上层策略做。

---

## 当前建议

现在最稳的协作方式是：

- **本小姐继续做 C# 和实机联调。**
- **Claude 从现在开始只接 Python MCP、`docs/api.md` 和 review。**
- 在 `T-C1` 完全收口前，Claude 不碰新 C# 功能。
