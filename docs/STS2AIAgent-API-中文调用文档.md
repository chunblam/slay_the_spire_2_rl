# STS2AIAgent HTTP API — 中文调用文档（完整清单）

> 适用范围：`RL_slay the spire` 项目通过本地 `STS2AIAgent` Mod 读取状态并执行动作。  
> 依据：`STS2-Agent/docs/api.md` + `STS2AIAgent` 当前源码路由/动作实现（含你之前要求的菜单自动化相关 API）。

---

## 1. 基础约定

| 项 | 说明 |
|---|---|
| 默认地址 | `http://127.0.0.1:18080`（你当前项目已改；若环境变量覆盖则以实际为准） |
| 协议 | HTTP + JSON |
| 推荐模式 | Session API（更适合 RL 状态机） |
| 兼容模式 | Legacy API（旧脚本可继续跑） |

---

## 2. 通用响应格式

### 成功响应

```json
{
  "ok": true,
  "request_id": "req_20260323_120000_0001",
  "data": {}
}
```

### 失败响应

```json
{
  "ok": false,
  "request_id": "req_20260323_120000_0002",
  "error": {
    "code": "invalid_action",
    "message": "Action is blocked in the current state.",
    "details": {},
    "retryable": true
  }
}
```

常见错误码：

- `invalid_request`：请求体缺字段或格式非法
- `not_found`：路由不存在
- `invalid_action`：当前状态不允许执行该动作
- `invalid_target`：传入索引/目标越界
- `state_unavailable`：状态暂不可读（通常可重试）
- `internal_error`：服务内部异常

---

## 3. 全部 HTTP 端点（已实现）

### `GET /health`

中文含义：健康检查，确认 Mod 是否成功启动并返回协议版本、游戏版本等信息。

### `GET /state`

中文含义：返回完整游戏状态快照（legacy 主状态端点）。

### `GET /actions/available`

中文含义：返回当前可执行动作与参数需求（legacy 动作发现端点）。

### `GET /events/stream`

中文含义：SSE 事件流端点，用于减少高频轮询（进阶用，RL 可选）。

### `POST /action`

中文含义：legacy 统一动作执行端点，body 中提供 `action` + 参数。

### `GET /api/v1/session/state`

中文含义：session 会话状态摘要端点，重点提供 `phase/can_act/block_reason/legal_actions`。

### `GET /api/v1/session/legal_actions`

中文含义：session 合法动作列表端点，适合直接生成 action mask。

### `POST /api/v1/session/action`

中文含义：session 统一动作执行端点（推荐）。

### `POST /api/v1/session/new_run`

中文含义：session 快捷端点，语义化触发“开始新局”（内部映射 `menu_new_run`）。

### `POST /api/v1/session/choose_character`

中文含义：session 快捷端点，语义化触发“选择角色”（内部映射 `menu_choose_character`）。

### `POST /api/v1/session/confirm`

中文含义：session 快捷端点，语义化触发“确认/开始”（内部映射 `menu_confirm`）。

### `POST /api/v1/session/return_to_menu`

中文含义：session 快捷端点，语义化触发“返回主菜单”（内部映射 `menu_return`）。

---

## 4. Session 模式字段说明（核心）

`GET /api/v1/session/state` 常用字段：

- `phase`：阶段（`menu | character_select | run | game_over | transition`）  
  中文含义：用于大粒度状态机切换。
- `screen`：当前界面  
  中文含义：当前可见/逻辑屏幕。
- `in_run`：是否在局内  
  中文含义：区分局外菜单流程和局内流程。
- `run_over`：是否已结算  
  中文含义：回合/整局结束后重开流程判断。
- `can_act`：当前是否允许发动作  
  中文含义：动作门控开关（关键）。
- `block_reason`：阻塞原因  
  中文含义：不可行动原因（动画、过场、弹窗等）。
- `legal_actions`：当前合法动作列表  
  中文含义：权威动作集合，建议直接用于 mask。

---

## 5. 全量动作清单（源码已实现）

> 下列动作可通过 `POST /action` 或 `POST /api/v1/session/action` 执行。  
> 每个动作后都附中文含义。

### 战斗动作

- `play_card`：打出一张手牌（常需 `card_index`，目标牌需 `target_index`）
- `end_turn`：结束当前回合
- `use_potion`：使用药水
- `discard_potion`：丢弃药水

### 奖励/地图/房间推进

- `claim_reward`：领取一项奖励
- `choose_reward_card`：在奖励牌中选一张
- `skip_reward_cards`：跳过奖励牌
- `collect_rewards_and_proceed`：自动收取可收奖励并继续
- `choose_map_node`：在地图可达节点中选择下一步
- `proceed`：点击继续按钮推进流程

### 宝箱/事件/营地

- `open_chest`：打开宝箱
- `choose_treasure_relic`：从宝箱中选择遗物
- `choose_event_option`：选择事件选项
- `choose_rest_option`：选择营地操作（休息/锻造等）

### 商店与牌组操作

- `open_shop_inventory`：打开商店库存面板
- `close_shop_inventory`：关闭商店库存面板
- `buy_card`：购买卡牌
- `buy_relic`：购买遗物
- `buy_potion`：购买药水
- `remove_card_at_shop`：购买商店删牌服务
- `select_deck_card`：在牌组选择界面选牌（删牌/升级等）
- `confirm_selection`：确认当前选择
- `close_cards_view`：关闭牌组浏览/选择视图

### 主菜单与时间线（你关心的自动开局链路重点）

- `continue_run`：继续已有存档局
- `abandon_run`：放弃当前局
- `open_character_select`：打开角色选择界面
- `select_character`：选定角色
- `embark`：确认开始新局
- `open_timeline`：打开时间线入口
- `choose_timeline_epoch`：选择时间线纪元
- `confirm_timeline_overlay`：确认时间线弹层
- `close_main_menu_submenu`：关闭主菜单子面板/返回上一层
- `return_to_main_menu`：返回主菜单

### 菜单语义别名（兼容你之前的门控方案）

- `menu_continue`：等价 `continue_run`（继续存档）
- `menu_new_run`：等价 `open_character_select`（开始新局）
- `menu_choose_character`：等价 `select_character`（选择角色）
- `menu_confirm`：动态等价 `embark` 或 `confirm_modal`（确认）
- `menu_back`：动态等价 `close_main_menu_submenu` 或 `dismiss_modal`（返回）
- `menu_return`：等价 `return_to_main_menu`（回主菜单）

### 弹窗/多人/调试动作

- `confirm_modal`：确认当前阻塞弹窗
- `dismiss_modal`：关闭/取消当前阻塞弹窗
- `host_multiplayer_lobby`：创建多人大厅
- `join_multiplayer_lobby`：加入多人大厅
- `ready_multiplayer_lobby`：多人大厅内就绪
- `disconnect_multiplayer_lobby`：退出多人大厅
- `unready`：取消就绪
- `increase_ascension`：提高进阶等级
- `decrease_ascension`：降低进阶等级
- `run_console_command`：执行调试控制台命令（仅调试场景）

---

## 6. RL 推荐调用流程（门控优先）

每一步建议如下：

1. `GET /api/v1/session/state`（读取 `can_act`）
2. 若 `can_act=false`，等待并重轮询
3. 读取 `legal_actions`（或调用 `GET /api/v1/session/legal_actions`）
4. 仅从合法动作中采样
5. `POST /api/v1/session/action`
6. 若返回 `pending` 或 `retryable=true`，继续轮询到稳定

中文含义：把“何时能点、能点什么”交给 Mod，避免 RL 在动画/过场中误发动作。

---

## 7. 自动开局/重开局最小闭环（菜单方案）

可参考动作链：

1. `menu_new_run` 或 `POST /api/v1/session/new_run`
2. `menu_choose_character` 或 `POST /api/v1/session/choose_character`
3. `menu_confirm` 或 `POST /api/v1/session/confirm`
4. 局内训练
5. `run_over` 后 `menu_return` 或 `POST /api/v1/session/return_to_menu`
6. 回到步骤 1

中文含义：实现“主菜单 -> 开局 -> 训练 -> 死亡后自动重开”的无人值守循环。

---

## 8. 端口与连通性检查

1. 游戏启动且 Mod 加载成功  
2. `http://127.0.0.1:18080/health` 可访问  
3. 若失败，优先看 `godot.log` 是否有 PCK 版本/加载错误  
4. `netstat -ano | findstr LISTENING | findstr :18080` 有输出才表示真正在监听

---

## 9. 参考

- `STS2-Agent/docs/api.md`
- `STS2-Agent/README.zh-CN.md`
- `STS2-Agent/STS2AIAgent/Server/Router.cs`
- `STS2-Agent/STS2AIAgent/Game/GameActionService.cs`

