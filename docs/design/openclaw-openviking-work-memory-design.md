# OpenClaw + OpenViking Work Memory Design / OpenClaw + OpenViking Work Memory 设计方案

## Status / 状态

- Document type / 文档类型: target-state design with current-implementation alignment / 目标态设计文档，结合当前实现对齐
- Scope / 范围: `examples/openclaw-plugin` + `openviking/session` + `openviking/server`
- Last updated / 更新日期: 2026-04-20

---

## Context / 背景

当前 OpenClaw + OpenViking 集成已经具备 Work Memory 的主链路能力：

- 插件可在 `afterTurn()` 中将新增消息写入 OpenViking session
- 服务端可在 `commit()` 中完成 archive、Working Memory 更新、memory extraction
- 插件可在 `assemble()` 中读取 `latest_archive_overview + live messages` 重建上下文
- 插件可在 `compact()` 中同步触发 commit，并以最新归档 WM 作为压缩历史摘要

但这套体系仍处于“主骨架已成型、正式设计尚未收口”的阶段。当前主要问题不是“有没有 Work Memory”，而是“有没有一份统一、稳定、可持续演进的端到端设计”。

当前需要解决的核心问题有四类：

1. 主拓扑已经在实现中形成，但缺少正式文档明确其边界与职责。
2. Memory quality 优先级需要正式落成规范，否则后续 guardrail、prompt、merge 行为容易各自演化。
3. 当前实现允许若干 section 长期单调增长，如果不补收敛策略，主 WM 会持续膨胀。
4. 工具证据底座仍在，但尚未成为默认上下文回注面的一等组成部分。

---

## Goals / 目标

本方案的目标是：

1. 明确 OpenClaw + OpenViking 端到端 Work Memory 的目标态拓扑。
2. 明确插件与服务端之间的职责边界。
3. 定义主 WM、recent tail、原始归档/证据层的分工。
4. 定义 Working Memory 的结构、更新协议、guardrail 和增长控制原则。
5. 给后续 archive expand、工具证据回溯、memory extraction 加固提供统一基线。

---

## Non-goals / 非目标

本方案不尝试解决以下问题：

- 重新设计 OpenClaw 的主运行时或工具执行框架
- 引入新的数据库或独立的 Work Memory 存储系统
- 要求主 WM 取代 archive、transcript、tool evidence 成为唯一事实来源
- 把 archive、tool trace、relations 在每次 assemble 时全部重新注入上下文
- 在本阶段完成所有 growth control 实现细节

---

## Decision Summary / 决策摘要

### Decision 1: Topology / 拓扑决策

目标态 Work Memory 固定采用三层结构：

1. `主 WM`
2. `最近 tail`
3. `原始归档/证据层`

这条决策已经在当前实现中基本成型，不再作为待选方案。

### Decision 2: Quality priority / 质量优先级

取舍优先级固定为：

1. 不丢失已确认的重要事实、决策、纠错记录
2. 不保留已经被明确推翻的陈旧信息
3. 对高风险、易变、关键结论保留可追溯路径
4. 在以上前提下控制主 WM 增长

### Decision 3: Ownership / 职责归属

- OpenClaw 插件负责 capture、trigger、assemble、compact orchestration
- OpenViking 服务端负责 archive、WM generation/update、merge、guardrail、memory extraction、evidence persistence

### Decision 4: Evidence strategy / 证据策略

工具证据与原始归档继续保留在底座中，但默认不要求主 WM 内联全部证据。目标态优先提供“可回查路径”，而不是把所有证据直接塞进主 WM。

### Decision 5: Growth policy / 增长策略

允许保守更新，但不接受主 WM 无限膨胀。当前实现可先保留 append-first 策略，但目标态必须提供 section-level consolidation 能力。

---

## Architecture / 总体架构

### High-level model / 高层模型

```text
OpenClaw turn
  |
  +-- afterTurn
  |     +-- write new messages/tools into OpenViking session
  |     +-- check pending_tokens
  |     +-- async commit with keepRecentCount=N
  |
  +-- assemble
  |     +-- read latest_archive_overview
  |     +-- read live tail messages
  |     +-- rebuild OpenClaw-facing context
  |
  +-- compact
        +-- sync commit with keepRecentCount=0
        +-- wait for Phase 2
        +-- read latest WM as compacted history
```

### Storage layers / 存储层次

```text
session/
  messages.jsonl              # live messages, including retained tail
  .meta.json                  # pending_tokens, keep_recent_count, etc.
  tools/{tool_id}/tool.json   # tool evidence objects
  history/archive_NNN/
    messages.jsonl            # archived raw messages
    .overview.md              # working memory
    .abstract.md              # short abstract
    .meta.json                # token stats
    .done                     # phase-2 completion marker
```

---

## Component 1: afterTurn / 组件 1：afterTurn

### Responsibility / 职责

`afterTurn()` 负责两件事：

1. 将本轮新增消息无损写入 OpenViking session
2. 在达到阈值后异步触发 commit

### Current implementation / 当前实现

当前链路已经是：

1. 提取新增消息
2. 转换为 OpenViking `parts`
3. 逐条写入 session
4. 读取服务端维护的 `pending_tokens`
5. 达阈值后调用 `commit(wait=false, keepRecentCount=cfg.commitKeepRecentCount)`

### Target behavior / 目标态

`afterTurn()` 继续保持“轻前台、重后台”：

- 前台优先写入成功
- commit 的 Phase 2 不阻塞当前轮返回
- recent tail 留在 live session 中，供下一轮直接消费

### Design note / 设计说明

`afterTurn()` 不是 compact 的替代品，它只负责渐进式归档和持续维护主 WM，不负责强制清空上下文。

---

## Component 2: assemble / 组件 2：assemble

### Responsibility / 职责

`assemble()` 负责把服务端已经持久化的历史状态重新组装成模型实际看到的上下文。

### Current implementation / 当前实现

当前 assemble 的实际输入面主要是：

1. `latest_archive_overview`
2. live messages
3. `Session Context Guide`

当前 archive 层默认只注入 `latest_archive_overview`，并不会自动读回 `tools/` 下的证据对象或 relations。

### Target behavior / 目标态

assemble 的默认结构保持简单：

1. `Session Context Guide`
2. 主 WM
3. live tail

其中：

- 主 WM 提供稳定历史连续性
- live tail 提供最近原始细节
- ToolPart 在 live tail 中继续还原成 `toolUse + toolResult`

### Design note / 设计说明

目标态下，assemble 默认不展开原始 archive 证据层；当主 WM 和 recent tail 不足时，再通过 expand 路径按需回查。

---

## Component 3: compact / 组件 3：compact

### Responsibility / 职责

`compact()` 是正式同步归档边界。

### Current implementation / 当前实现

当前 compact 已经走：

1. `commit(wait=true, keepRecentCount=0)`
2. 等待 Phase 2 完成
3. 重新读取 `getSessionContext()`
4. 使用最新 `latest_archive_overview` 作为 compact 结果

### Target behavior / 目标态

compact 继续承担以下职责：

- 把当前所有 live messages 纳入主 WM
- 最大化回收上下文预算
- 形成新的正式历史边界

### Design note / 设计说明

compact 不维护第二套独立 summary。主 WM 就是 compact 后的正式历史摘要。

---

## Working Memory Schema / Working Memory 结构

### Section layout / 分段结构

目标态主 WM 固定采用 7 section：

1. `Session Title`
2. `Current State`
3. `Task & Goals`
4. `Key Facts & Decisions`
5. `Files & Context`
6. `Errors & Corrections`
7. `Open Issues`

### Why fixed sections / 为什么固定分段

固定 section 的收益是：

- 便于增量更新
- 便于服务端 merge
- 便于 section 级 guardrail
- 便于后续测试与诊断

### Update protocol / 更新协议

每个 section 只允许三种 op：

- `KEEP`
- `UPDATE`
- `APPEND`

默认优先级：

`KEEP > APPEND > UPDATE`

### Section semantics / 分段语义

#### `Current State`

- 描述最新状态
- 应短、应新、应可替换
- 默认 `UPDATE`

#### `Task & Goals`

- 描述会话主目标
- 相对稳定
- 仅在范围或目标变化时更新

#### `Key Facts & Decisions`

- 记录 durable facts、constraints、technical decisions
- 不能因为只讨论了局部新信息，就静默丢失旧事实

#### `Files & Context`

- 只记录未来回答真正依赖的资源
- 关键文件路径不能静默消失
- 大批量 URL / 媒资 / 搜索结果应被摘要化而不是原样累积

#### `Errors & Corrections`

- 记录错误、误判、纠正、失败路径
- 已解决错误仍属于历史，不应静默消失

#### `Open Issues`

- 记录未决问题、风险、待跟进项
- 已解决项应显式标记 resolved，而不是静默删除

---

## Merge Guardrails / 合并保护规则

### Current implemented guardrails / 当前已实现

当前代码中已经落地的 guardrails 包括：

- `Errors & Corrections` append-only
- `Key Facts & Decisions` append-only
- `Files & Context` 不允许静默丢失已有文件路径
- `Session Title` 防止主题漂移
- `Open Issues` 不允许静默删除旧项

### Why these exist / 为什么需要这些规则

这些规则的目的不是“让 LLM 更自由改写”，而是“在 prompt 和 model 不稳定时，先把 memory regression 风险压住”。

---

## Growth Control / 增长控制

### Design requirement / 设计要求

目标态明确要求：

- 主 WM 可以增长
- 但不能无限增长

### Current behavior / 当前行为

当前实现下，主 WM 在部分 section 上会长期单调增长：

- `Key Facts & Decisions`
- `Errors & Corrections`
- `Files & Context` 在持续追加新路径时也会增长

这不是理论风险，而是当前行为特征。新增 characterization tests 已经验证了这一点。

### Target behavior / 目标态

增长控制应当采用 section-level policy：

- `Current State` 可重写收敛
- `Task & Goals` 保持稳定，低频变化
- `Key Facts & Decisions` 在超预算时允许安全 consolidation
- `Files & Context` 在超预算时优先删冗余引用与 bulk URLs
- `Errors & Corrections` 保持历史，但后续可引入结构化压缩

### Important note / 重要说明

当前实现中的 append-only 策略更像“防丢失保护”，不是最终的 growth control 方案。

---

## Evidence and Provenance / 证据与可追溯性

### Current substrate / 当前底座

当前系统仍保留工具证据与回溯底座：

1. ToolPart 进入 session / archive messages
2. `tools/{tool_id}/tool.json` 保存工具输入输出与元数据
3. relations 继续记录已使用资源和技能

### Design decision / 设计决策

目标态不要求主 WM 的每条 durable fact 都内联原始证据。

目标态要求的是：

- 关键结论有可回查路径
- 工具证据能被展开
- archive 能被重新打开

### Priority for evidence anchors / 优先带锚点的内容

优先保留可追溯路径的内容包括：

- 纠错项
- 高风险、易变事实
- 关键决策及其依据
- 基于工具读取/检索/执行得出的结论

---

## Current Implementation vs Target / 当前实现与目标态差距

### Already landed / 已经落地

- `主 WM + recent tail` 主拓扑已经进入实现
- `keep_recent_count` 已打通插件与服务端
- `pending_tokens` 滑动窗口已实现
- WM v2 的 7-section + tool-call update + server merge 已进入主实现
- compact 与 afterTurn 的职责边界已经清楚
- 工具证据持久化底座仍在

### Still missing / 尚未收口

- 正式的 design doc 规范化表达
- section-level dynamic reminders
- `Key Facts & Decisions` 的安全 consolidation
- `Files & Context` 的 bulk URL pruning
- 轻量 evidence anchor 规范
- 完整的 growth control 实现

### Important interpretation / 重要判断

对于工具回溯能力，当前状态更准确的定义是：

`底层证据资产仍在，但默认上下文回注面尚未完整打通`

这不是一个单纯的开关问题，而是一个尚未完全闭环的产品面问题。

---

## Rollout Plan / 演进顺序

### Phase 1 / 第一阶段

收口当前主链路：

- 固化正式设计文档
- 补齐 WM v2 guardrail 和测试
- 明确 section-level growth control 策略

### Phase 2 / 第二阶段

补齐可控收敛能力：

- `Key Facts & Decisions` 的安全 consolidation
- `Files & Context` 的 bulk URL pruning
- dynamic section reminders

### Phase 3 / 第三阶段

补齐按需证据回查体验：

- archive expand
- tool evidence expand
- relation-based jump

---

## Conclusion / 结论

OpenClaw + OpenViking 的 Work Memory 目标态，不是一个无限膨胀的 summary 文档，也不是完全依赖近期原文的临时上下文。

它应当是一个分层系统：

- 主 WM 负责长期、结构化、可持续演进的工作状态
- recent tail 负责最近细节与工具轨迹
- archive / tool evidence / relations 负责精确回溯

当前实现已经把这套结构的主骨架搭起来了。接下来的重点不是重新讨论方向，而是把它从“方向正确的中间态实现”推进到“边界清楚、预算可控、证据策略明确的正式系统”。
