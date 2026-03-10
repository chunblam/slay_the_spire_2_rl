# Phase 6 实机验证记录模板

把这份模板复制成一次真实验证记录，用来沉淀“是否达到可发布”的最终证据。

---

## 基本信息

- 验证日期：
- 验证人：
- Git commit：
- 游戏版本：
- Mod 构建配置：`Release`
- MCP 启动方式：
- 备注：

---

## 静态预检

- [ ] `preflight-release.ps1` 通过
- [ ] `build-mod.ps1 -Configuration Release` 通过
- [ ] `test-mod-load.ps1 -DeepCheck` 通过

输出摘要：

```text
粘贴关键输出
```

---

## 链路验证

### 1. 开局链路

- [ ] 如存在存档：`continue_run` 或 `abandon_run`
- [ ] `MAIN_MENU -> open_character_select`
- [ ] 如单人入口被时间线门控：`open_timeline -> close_main_menu_submenu -> open_character_select`
- [ ] 如时间线存在可解锁 epoch：`choose_timeline_epoch -> confirm_timeline_overlay`
- [ ] `CHARACTER_SELECT` 可读取
- [ ] `select_character`
- [ ] `embark`
- [ ] `MODAL` / FTUE 可处理
- [ ] 成功进入 `MAP`

结果：

```text
记录 screen 变化、available_actions、异常情况
```

### 2. 地图与战斗链路

- [ ] `choose_map_node`
- [ ] `play_card`
- [ ] `end_turn`
- [ ] `use_potion` 或 `discard_potion`
- [ ] 成功结束战斗

结果：

```text
记录战斗对象索引、药水行为、是否有 pending 状态
```

### 3. 奖励链路

- [ ] `claim_reward`
- [ ] `choose_reward_card`
- [ ] `skip_reward_cards`
- [ ] `collect_rewards_and_proceed`

结果：

```text
记录奖励子流程切换是否稳定
```

### 4. 宝箱链路

- [ ] `open_chest`
- [ ] `choose_treasure_relic`
- [ ] `proceed`

结果：

```text
记录 relic 选择后状态变化
```

### 5. 事件链路

- [ ] 普通事件选项
- [ ] `event.is_finished=true` 后离开
- [ ] 事件嵌套战斗

结果：

```text
记录事件切换、嵌套战斗、是否残留旧状态
```

### 6. 休息点链路

- [ ] `choose_rest_option` -> `HEAL`
- [ ] `choose_rest_option` -> `SMITH`
- [ ] `select_deck_card`
- [ ] `proceed`

结果：

```text
记录 smith 进入选牌后是否顺利返回
```

### 7. 商店链路

- [ ] `open_shop_inventory`
- [ ] `buy_card`
- [ ] `buy_relic`
- [ ] `buy_potion`
- [ ] `remove_card_at_shop`
- [ ] `close_shop_inventory`
- [ ] `proceed`

结果：

```text
记录内外层状态、价格变化、删牌返回路径
```

### 8. 收尾链路

- [ ] 进入 `GAME_OVER`
- [ ] `game_over` 字段完整
- [ ] `return_to_main_menu`

结果：

```text
记录退出是否稳定，是否卡总结页
```

---

## 问题清单

| 严重度 | 场景 | 现象 | 复现步骤 | 预估根因 |
| --- | --- | --- | --- | --- |
| P0/P1/P2 |  |  |  |  |

---

## 结论

- [ ] 可以发布
- [ ] 可以灰度试用，但仍有已知限制
- [ ] 不能发布，需继续修复

结论说明：

```text
写清楚阻塞项，不要只写“基本可用”
```
