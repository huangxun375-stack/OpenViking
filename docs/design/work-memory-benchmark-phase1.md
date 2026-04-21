# Work Memory Benchmark Phase 1 Design / Work Memory Benchmark 第一阶段设计

## Status / 状态
- Document type / 文档类型: phase-1 benchmark design / 第一阶段 benchmark 设计
- Scope / 范围: `benchmark/work_memory` + OpenClaw plugin + OpenViking service
- Last updated / 更新日期: 2026-04-20

---

## Context / 背景

当前 OpenClaw + OpenViking 已经形成 `主 WM + recent tail` 的端到端 Work Memory 架构：

- OpenClaw 在 `afterTurn()` 中写入新增消息，并按阈值异步触发 `commit`
- OpenViking 在后台完成 archive、Working Memory 更新与 memory extraction
- OpenClaw 在 `assemble()` 中使用 `latest_archive_overview + live messages` 组装上下文
- OpenClaw 在 `compact()` 中同步等待 commit 完成，并使用最新 Working Memory 作为压缩后的历史摘要

仓库内已经存在两类相关评测资产：

1. 外部数据集评测链路，如 `benchmark/locomo` 和 `benchmark/RAG`
2. 单元测试与回归测试，如 `tests/unit/session/test_working_memory_growth.py` 和 `tests/oc2ov_test/tests/long_term/test_long_term_conversation.py`

但它们仍然无法完整回答当前 Work Memory 体系最核心的内部问题：

1. `recent tail -> Working Memory` 的交接是否稳定
2. 更正后的新事实是否能压过旧事实，而不是在 WM 中长期并存
3. tool result 进入 tail、archive、WM 后是否还能支持正确回答
4. `Files & Context` 是否能保留关键上下文并裁掉噪声
5. 主 WM 是否持续膨胀，以及膨胀主要发生在哪些 section

因此需要一套更贴合当前实现的内部 benchmark。这个 benchmark 的目标不是替代 LoCoMo、LongMemEval 等对外可比基线，而是专门测当前 `WM + tail` 架构的正确性、交接质量和增长控制。

---

## Goals / 目标

第一阶段 benchmark 需要满足以下目标：

1. 端到端覆盖当前 `WM + recent tail` 架构最关键的行为面
2. 同时支持黑盒结果评测与白盒 artifact 断言
3. 优先发现当前实现中的回归、膨胀和更正失败问题
4. 结构上可逐步扩展到后续 phase，而不是一次性做成大而全系统
5. 复用仓库现有 benchmark 目录风格、OpenClaw 链路和单测经验

---

## Non-goals / 非目标

第一阶段 benchmark 不尝试覆盖以下范围：

- 取代 LoCoMo、LongMemEval、MemBench 等公开 benchmark
- 完整覆盖多模态记忆、shared memory、multi-agent memory
- 在本阶段实现 archive expand 的完整自动回查评测
- 要求主 WM 为每条事实都绑定完整 provenance 链
- 在第一阶段就把 benchmark 变成强制 CI gate

---

## Decision Summary / 决策摘要

### Decision 1: Benchmark style / Benchmark 形态

采用混合式 benchmark：

1. 外部 benchmark 继续保留用于对外可比
2. 新增内部 `benchmark/work_memory` 专门评测当前 `WM + tail` 架构

### Decision 2: Phase-1 size / 第一阶段规模

第一阶段固定为 `24` 个 case，优先覆盖当前最容易出错的能力面，而不是追求全面。

### Decision 3: Evaluation model / 评测模型

采用 checkpoint-driven 评测。每个 case 不是只看最后一问，而是在多个关键时点进行验证：

- `after_turn`
- `after_async_commit`
- `after_compact`

### Decision 4: Mixed visibility / 黑盒 + 白盒

每个 case 可以包含：

- 黑盒问题回答断言
- Working Memory 文档断言
- recent tail / tool artifact 断言
- size / budget 断言

### Decision 5: Diagnostics first / 诊断优先

第一阶段优先输出高质量诊断结果，不立即将全部 case 作为 CI hard gate。已知不满足的行为允许先通过 `expected_failure` 或 capability-level reporting 暂时记录。

---

## Architecture Surface Under Test / 被测架构面

### Current flow / 当前链路

```text
User turn
  -> OpenClaw afterTurn
       -> write messages / tool parts
       -> maybe trigger async commit
  -> OpenViking commit
       -> archive old messages
       -> update Working Memory
       -> keep recent tail
  -> OpenClaw assemble
       -> latest_archive_overview + live messages
  -> OpenClaw compact
       -> sync commit
       -> latest Working Memory
```

### Benchmark focus / 第一阶段重点测面

第一阶段 benchmark 重点覆盖以下 5 个被测面：

1. `Tail surface`
   刚发生的信息是否在 recent tail 中可用
2. `Handoff surface`
   背景 commit 后信息是否能稳定迁移到 Working Memory
3. `Compact durability surface`
   compact 后历史状态是否仍可恢复
4. `Artifact surface`
   WM section、tail、tool artifact 是否符合预期
5. `Growth surface`
   WM 是否持续膨胀，以及是否突破预算

---

## Phase-1 Suite Definition / 第一阶段套件定义

### Capability coverage / 能力覆盖

第一阶段 suite 固定为 `24` 个 case，分为 6 组：

1. `Tail Recall` - `6` 个
2. `WM Durable Recall` - `6` 个
3. `Correction & Overwrite` - `4` 个
4. `Tool-Grounded Memory` - `4` 个
5. `Files & Context Pruning` - `2` 个
6. `Growth & Budget` - `2` 个

### Execution modes / 执行模式

为降低第一阶段实现成本，同时提高诊断清晰度，固定使用 3 种执行模式：

1. `chat_e2e`
   通过 OpenClaw 正常发送消息并提问，模拟真实使用路径
2. `session_inject`
   直接向 session 注入精确消息或 ToolPart，用于构造高确定性的工具证据场景
3. `artifact_whitebox`
   直接调用或读取底层 WM artifact，用于增长和 merge 行为的白盒评测

### Mode allocation / 模式分配

- `18` 个 `chat_e2e`
- `4` 个 `session_inject`
- `2` 个 `artifact_whitebox`

这种分配对应到能力组：

- `Tail Recall`：`6 x chat_e2e`
- `WM Durable Recall`：`6 x chat_e2e`
- `Correction & Overwrite`：`4 x chat_e2e`
- `Tool-Grounded Memory`：`4 x session_inject`
- `Files & Context Pruning`：`2 x chat_e2e`
- `Growth & Budget`：`2 x artifact_whitebox`

---

## Case Inventory / Case 清单

### Group 1: Tail Recall / 最近 tail 回忆

1. `tail_fact_single`
   最近一轮新写入的事实在 `after_turn` 时可直接回答
2. `tail_task_state_recent`
   最近变更的任务状态在 `after_turn` 时可直接回答
3. `tail_recent_file_focus`
   最近讨论的文件/模块在 `after_turn` 时可直接回答
4. `tail_recent_decision`
   最近刚做出的决策在 `after_turn` 时可直接回答
5. `tail_recent_open_issue`
   最近刚暴露的问题在 `after_turn` 时可直接回答
6. `tail_recent_goal_shift`
   最近刚更新的目标方向在 `after_turn` 时可直接回答

### Group 2: WM Durable Recall / WM 持久回忆

7. `wm_durable_fact_after_compact`
   compact 后旧事实仍能从 WM 恢复
8. `wm_durable_task_goal_after_compact`
   compact 后任务目标仍连续
9. `wm_durable_file_context_after_compact`
   compact 后关键文件上下文仍保留
10. `wm_durable_decision_after_many_turns`
   多轮干扰后关键决策仍可恢复
11. `wm_cross_session_state_after_compact`
   跨 session 状态在 compact 后仍可回答
12. `wm_open_issue_continuity_after_compact`
   compact 后未解决问题仍能继续追踪

### Group 3: Correction & Overwrite / 更正与覆盖

13. `correction_current_owner`
   当前负责人被更正后只能回答新值
14. `correction_file_path_renamed`
   文件路径变更后旧路径不能再被当作当前路径
15. `correction_task_status`
   任务状态从 blocked 改为 fixed 后不能继续答 blocked
16. `correction_preference_override`
   用户偏好变化后旧偏好不能继续作为当前偏好

### Group 4: Tool-Grounded Memory / 工具证据记忆

17. `tool_result_fact_inject`
   关键事实来自 tool result，而不是普通聊天文本
18. `tool_result_status_change_inject`
   tool result 中的状态更新需要覆盖旧状态
19. `tool_error_then_fix_inject`
   先出现 tool error，再出现修复结果，最终回答必须跟随修复后状态
20. `tool_generated_file_inject`
   tool 输出生成的关键文件路径应进入可用上下文

### Group 5: Files & Context Pruning / 文件与上下文裁剪

21. `files_keep_key_paths_drop_bulk_urls`
   保留关键路径，裁掉大批量 URL 噪声
22. `files_keep_active_modules_drop_noise_refs`
   保留活跃模块，裁掉低相关引用堆积

### Group 6: Growth & Budget / 增长与预算

23. `growth_append_only_key_facts`
   连续 append 后 `Key Facts & Decisions` 的增长趋势可被稳定观测
24. `growth_files_context_budget`
   `Files & Context` 在重复加入唯一引用时的增长与预算突破可被稳定观测

---

## Checkpoint Model / Checkpoint 模型

### Supported checkpoints / 支持的 checkpoint

第一阶段固定支持 3 类 checkpoint：

1. `after_turn`
   本轮消息写入并产生响应后立即检查
2. `after_async_commit`
   等待后台 commit 完成后检查
3. `after_compact`
   主动触发 compact，等待完成后检查

### Commit completion detection / commit 完成判定

第一阶段不新增 benchmark-only 等待接口，而是直接复用当前仓库已存在的异步 commit 机制：

1. runner 调用 `POST /api/v1/sessions/{session_id}/commit`，固定使用 `wait=false`
2. 如果返回 `task_id`，runner 轮询 `GET /api/v1/tasks/{task_id}`
3. 当 task 状态变为 `completed` 时，视为 `after_async_commit` 可检查
4. 当 task 状态变为 `failed` 时，checkpoint 记为 infra failure

第一阶段默认参数：

- poll interval: `500ms`
- commit poll timeout: `300s`
- transient poll errors tolerance: `3` consecutive failures before marking infra failure

如果服务端未返回 `task_id`，runner 采用降级策略，而不是发明新协议：

1. 记录 `infra_warning`
2. 在最多 `30s` 内轮询 `GET /api/v1/sessions/{session_id}/context`
3. 当 `latest_archive_overview` 从空变为非空，或与 commit 前快照发生变化时，视为 archive visibility 已出现，可继续 checkpoint

这样做的目标不是证明 Phase 2 一定完全完成，而是保证第一阶段 smoke runner 有一条与当前实现兼容的、可操作的等待路径。

### Semantics / 语义

#### `after_turn`

- 主要用于验证 `recent tail`
- 允许问题直接由 live messages 支撑
- 不要求此时 Working Memory 已完成迁移

#### `after_async_commit`

- 主要用于验证异步交接边界
- 关注事实是否已进入可持久层
- 在当前实现中，回答仍可能同时受到 tail 影响，因此该 checkpoint 更适合作为 handoff 观察点，而非严格 source exclusivity 检查点

#### `after_compact`

- 主要用于验证 durable memory
- 要求信息在 recent tail 清理后仍能恢复
- 是第一阶段判断 `WM required` 的最强 checkpoint

### Handoff principle / 交接原则

第一阶段不强制回答来源必须可唯一归因到 `tail` 或 `WM`。更现实的做法是：

1. 用 `after_turn` 测最近信息能否立即被利用
2. 用 `after_async_commit` 测交接是否发生
3. 用 `after_compact` 测信息在 tail 回收后是否仍存活

据此定义 `handoff_success_rate`，而不是在 phase-1 试图做“回答来源唯一归因”。

---

## Case Schema / Case 数据结构

### File format / 文件格式

第一阶段 case 使用 YAML，路径位于：

```text
benchmark/work_memory/cases/*.yaml
```

### Minimal schema / 最小 schema

```yaml
case_id: correction_current_owner
title: Current owner correction survives compact
mode: chat_e2e
capability: correction
priority: critical
tags: [overwrite, compact]
expected_failure: false

turns:
  - role: user
    content: "记住：当前负责人是 Alice。"
  - role: user
    content: "更正一下，当前负责人改成 Bob，Alice 是之前的负责人。"

checkpoints:
  - id: cp_after_turn
    trigger: after_turn
    query: "现在的负责人是谁？"
    expected_any: ["Bob", "鲍勃"]
    forbidden_any: ["Alice", "艾丽斯"]
    source_expectation: tail

  - id: cp_after_compact
    trigger: after_compact
    query: "现在的负责人是谁？"
    expected_any: ["Bob", "鲍勃"]
    forbidden_any: ["Alice", "艾丽斯"]
    source_expectation: wm_required

artifact_assertions:
  wm_must_contain:
    - "Bob"
  wm_may_contain_only_as_historical:
    - "Alice"

budget_assertions:
  max_total_wm_tokens: 4000
```

### Common fields / 通用字段

- `case_id`: 全局唯一 case 标识
- `title`: 简短标题
- `mode`: `chat_e2e` / `session_inject` / `artifact_whitebox`
- `capability`: 能力分组
- `priority`: `critical` / `normal`
- `tags`: 辅助筛选标签
- `expected_failure`: 用于记录当前已知失败但需要持续跟踪的 case

### Turn fields / turn 字段

`chat_e2e` 和 `session_inject` 模式下都允许 `turns`，但语义不同：

- `chat_e2e`: 由 runner 通过 OpenClaw 正常发送
- `session_inject`: 由 runner 直接写入 session message / ToolPart

### Checkpoint fields / checkpoint 字段

- `id`
- `trigger`
- `query`
- `expected_any`
- `forbidden_any`
- `source_expectation`

其中 `source_expectation` 仅用于报告和诊断，不在第一阶段承担严格 source attribution 责任。可选值：

- `any`
- `tail`
- `wm_candidate`
- `wm_required`
- `tool_evidence`

### Matching policy / 匹配判定

第一阶段默认不使用 LLM judge。`expected_any` / `forbidden_any` 采用确定性匹配，避免在内部 benchmark 中引入额外 judge 波动。

默认规则如下：

1. 对回答文本与目标短语做轻量规范化
   - trim
   - lowercase
   - collapse whitespace
   - strip common surrounding punctuation
2. `expected_any`
   - 规范化后命中任意一个候选短语，则视为匹配成功
3. `forbidden_any`
   - 规范化后命中任意一个候选短语，则视为泄漏

第一阶段不自动做中英文同义改写推断。像 `"Bob"` / `"鲍勃"` 这类别名，需要显式写入 `expected_any` 或 `forbidden_any`。这是有意为之：phase-1 优先追求稳定和可解释，而不是语义判定的最大召回。

后续 phase 可按需扩展：

- `match_mode: exact_phrase`
- `match_mode: regex`
- `match_mode: llm_judge`

### Artifact assertions / artifact 断言

第一阶段允许以下白盒断言类型：

- `wm_must_contain`
- `wm_must_not_contain`
- `wm_may_contain_only_as_historical`
- `section_contains`
- `section_not_contains`
- `tail_min_messages`
- `tool_pair_count`
- `key_file_paths_present`

### Artifact assertion semantics / artifact 断言语义

为了避免模糊断言，第一阶段对白盒断言的语义固定如下：

- `wm_must_contain`
  - 目标字符串必须出现在 Working Memory 文本中
- `wm_must_not_contain`
  - 目标字符串不能出现在 Working Memory 文本中
- `wm_may_contain_only_as_historical`
  - 目标字符串允许出现，但只能出现在带有历史/过时标记的 bullet 或段落中
  - phase-1 默认历史标记包括：
    - `previous`
    - `former`
    - `old`
    - `historical`
    - `superseded`
    - `之前`
    - `旧`
    - `曾经`
    - `历史`

这条断言用于表达“旧事实可以作为纠错记录存在，但不能继续被视为当前有效状态”。

### Budget assertions / 预算断言

第一阶段允许以下预算断言：

- `max_total_wm_tokens`
- `max_section_tokens`
- `max_section_items`

---

## Metrics / 指标设计

### Metric layers / 指标分层

第一阶段指标分为两层：

1. `black-box metrics`
   关注用户实际感知结果
2. `white-box metrics`
   关注 WM / tail / tool artifact 内部状态

### Black-box metrics / 黑盒指标

- `case_pass_rate`
- `checkpoint_pass_rate`
- `capability_pass_rate`
- `answer_match_rate`
- `forbidden_fact_leak_rate`
- `correction_accuracy`
- `tool_grounded_accuracy`
- `abstention_accuracy`

其中：

- `answer_match_rate` 表示回答是否命中 `expected_any`
- `forbidden_fact_leak_rate` 表示回答中是否出现 `forbidden_any`
- `correction_accuracy` 是第一阶段一级指标

### White-box metrics / 白盒指标

- `wm_fact_coverage`
- `stale_fact_leakage`
- `tail_retention_score`
- `tool_pair_integrity`
- `file_path_retention`
- `bulk_reference_prune_rate`
- `wm_growth_slope`
- `section_budget_violation_count`

其中：

- `wm_growth_slope` 表示每轮新增信息后 WM 的 token 增长趋势
- `section_budget_violation_count` 表示 section 预算突破次数

### Growth measurement policy / 增长测量策略

虽然第一阶段只有 `2` 个 dedicated growth case，但 `growth` 不是只从这两个 case 中采样。

第一阶段的增长指标由两部分组成：

1. `dedicated stress probes`
   - `growth_append_only_key_facts`
   - `growth_files_context_budget`
2. `cross-suite growth telemetry`
   - 所有 case、所有 checkpoint 都记录 Working Memory token 数、section item 数和 section token 数

因此：

- `2` 个 growth case 负责做高强度、可重复的增长压力测试
- 全 suite 的 side-effect 负责提供更真实的增长曲线观察

### Phase-1 priority metrics / 第一阶段一级指标

第一阶段重点关注以下 4 个一级指标：

1. `handoff_success_rate`
   同一事实在 `after_turn` 与 `after_compact` 两侧都成立，说明 `tail -> WM` 交接成功
2. `overwrite_correctness`
   更正后只保留新事实的正确率
3. `tool_grounded_accuracy`
   基于工具结果的事实能否被正确利用
4. `wm_growth_slope`
   主 WM 的增长趋势是否可控

### Case pass rule / case 通过规则

第一阶段采用以下规则：

- 一个 case 中所有 `required` checkpoint 通过，则 case 通过
- 任一 `forbidden_any` 命中，则该 checkpoint 失败
- 任一 required artifact assertion 失败，则该 checkpoint 失败
- `growth` 组 case 允许先报告为诊断项，而不是 hard pass/fail gate

### Flakiness policy / 波动控制策略

第一阶段的官方基线仍然是单次运行，避免 benchmark 成本一开始就过高。

默认策略：

- `run_count = 1`
- 不默认 majority vote
- 不默认多次重试覆盖真实失败

runner 需要支持可选参数：

- `--repeat N`

当 `N > 1` 时：

- `chat_e2e` 和 `session_inject` 使用 majority pass 作为聚合结果
- `artifact_whitebox` 使用 all-pass 作为聚合结果
- 报告中同时保留原始 `pass_count / N`

`expected_failure` 不自动摘帽。第一阶段采用保守策略：

- 只有在连续 `3` 次 benchmark run 中都达到聚合通过阈值，且人工确认后，才移除 `expected_failure`

---

## Reporting / 报告输出

### Output files / 输出文件

每次 benchmark run 生成以下产物：

```text
benchmark/work_memory/results/<run_id>/
  summary.json
  checkpoints.csv
  capability_scores.json
  growth.csv
  report.md
  artifacts/<case_id>/<checkpoint_id>/
```

### Artifact snapshots / artifact 快照

每个 checkpoint 尽量保存以下快照：

- question / answer
- parsed WM sections
- latest archive overview
- retained tail summary
- tool artifact summary
- token / item budgets

这样可以在失败后直接判断问题出在：

- 没写进去
- 写进去了但没有合并
- 合并了但仍保留陈旧事实
- 回答时没利用到
- 文档持续膨胀

---

## Runner Architecture / Runner 架构

### File layout / 文件布局

```text
benchmark/work_memory/
  README.md
  run.py
  cases/
    *.yaml
  src/
    loader.py
    runner.py
    assertions.py
    probes.py
    report.py
    executors/
      chat_e2e.py
      session_inject.py
      artifact_whitebox.py
  results/
```

### Responsibilities / 职责划分

- `run.py`
  CLI 入口，负责参数解析、case 选择和 run 启动
- `loader.py`
  读取 YAML case 并做 schema 校验
- `executors/chat_e2e.py`
  复用 OpenClaw API 链路发送消息和提问
- `executors/session_inject.py`
  直接构造 session message / ToolPart
- `executors/artifact_whitebox.py`
  直接运行白盒增长与 merge 检查
- `probes.py`
  读取 Working Memory、tail、tool artifact 和 token 预算
- `assertions.py`
  实现 checkpoint 级断言和 metric 计算
- `report.py`
  输出 JSON / CSV / Markdown 报告

### Case isolation policy / case 隔离策略

第一阶段固定采用“每个 case 独立 session”的策略，不共享 session。

原因很直接：

- 共享 session 会让 case 顺序相互污染
- correction 类 case 容易受到前序事实影响
- growth 类 case 需要明确知道增长来自当前 case，而不是之前的残留

具体策略：

1. 每个 case 使用独立的 `session_id` 或 `session_key`
2. `session_id` 需带 `run_id + case_id` 前缀，便于结果归档与排查
3. 同一 case 内的多个 checkpoint 共享同一 session
4. case 结束后允许 best-effort cleanup，但 benchmark 的正确性不依赖 cleanup 成功

### OpenClaw interaction contract / OpenClaw 交互约定

第一阶段 `chat_e2e` 不新增新的交互协议，而是直接复用现有 LoCoMo/OpenClaw benchmark harness 的请求方式。

默认路径：

1. 使用 OpenClaw 的 `POST /v1/responses`
2. 通过 `X-OpenClaw-Agent-ID` 指定 agent
3. 通过 `X-OpenClaw-Session-Key` 维持 case 内会话连续性

这与现有 `benchmark/locomo/openclaw/eval.py` 中的发送路径保持一致，能降低 Step 1 的实现分歧。

### Reuse strategy / 复用策略

第一阶段优先复用现有仓库资产：

- 参考 `benchmark/locomo/openclaw/eval.py` 的请求与评测入口风格
- 参考 `benchmark/RAG/run.py` 的 benchmark CLI 风格
- 参考 `tests/unit/session/test_working_memory_growth.py` 的白盒增长检查思路
- 参考 `tests/oc2ov_test/tests/long_term/test_long_term_conversation.py` 的长程回忆用例组织方式
- 复用 `OpenVikingClient.commitSession()` 已有的 `task_id -> GET /api/v1/tasks/{task_id}` 轮询模式，而不是新写一套 Phase 2 等待逻辑

---

## Rollout Plan / 落地顺序

### Step 1: Schema and smoke runner / schema 与最小 runner

先完成：

- case schema
- loader
- `chat_e2e` 最小闭环
- `8` 个 smoke cases

目标是先打通一条完整链路。

### Step 2: Artifact probes / artifact probe

补齐：

- WM section 读取
- tail 摘要读取
- tool artifact 读取
- token / item budget 统计

目标是让 benchmark 从“只知道答错了”升级到“知道为什么错”。

### Step 3: Full phase-1 suite / 完整 phase-1 套件

将 case 扩充到完整 `24` 个，并补上：

- `session_inject`
- `artifact_whitebox`
- growth / budget 报告

### Step 4: CI policy / CI 策略

第一阶段完成后，再决定哪些 case 进入 CI：

- `critical` 类能力优先
- `growth` 类先保留为 report-only
- 已知失败行为先以 `expected_failure` 形式保留

---

## Risks and Trade-offs / 风险与取舍

### Trade-off 1: Source attribution / 来源归因

第一阶段不做严格回答来源归因，因为当前 `after_async_commit` 阶段的回答仍可能同时受到 tail 与 WM 影响。直接把“回答必须来自 WM”做成硬规则，会让 benchmark 过于脆弱。

取而代之的是：

- 用 checkpoint 序列测交接
- 用 artifact 断言测持久化
- 用 compact 后回答测 durable memory

### Trade-off 2: Tool coverage / 工具覆盖

第一阶段的 tool 能力主要通过 `session_inject` 验证，以降低 flaky 风险。等端到端 tool trace 回注面更稳定后，再扩展为更多 `chat_e2e` tool cases。

这是一种明确的 phase-1 取舍，而不是目标态。phase-2 应逐步用“真实 tool execution + chat_e2e”替换一部分 `session_inject` case，以验证真实 tool result 格式、体量和嵌套结构下的表现。

### Trade-off 3: Growth gating / 增长 gating

当前实现已知存在增长问题，因此 phase-1 中增长 case 的主要价值是诊断，而不是立即充当 merge gate。否则 benchmark 会在真正具备收敛机制前一直阻塞开发。

---

## Success Criteria / 第一阶段成功标准

如果第一阶段 benchmark 达到以下条件，可认为 phase-1 设计目标达成：

1. 可以稳定运行全部 `24` 个 case
2. 每个失败 case 都能产出可读的 artifact 和原因定位线索
3. 能单独汇总 `tail`、`handoff`、`overwrite`、`tool-grounded`、`growth` 五类问题
4. 能明确区分“回答错了”和“WM 本身写坏了”
5. 能为下一步实现计划提供明确的优先级输入

---

## Conclusion / 结论

第一阶段 `Work Memory Benchmark` 的核心不是“做一个通用 memory leaderboard”，而是围绕当前 `主 WM + recent tail` 架构，优先验证最关键的 4 件事：

1. 最近信息能不能立刻用
2. 最近信息能不能稳定交接到主 WM
3. 更正与工具结果能不能压过陈旧事实
4. 主 WM 会不会持续膨胀

为此，phase-1 采用 `24 case + 3 execution modes + 3 checkpoint types + 黑盒/白盒双层指标` 的方案，在保证实现成本可控的前提下，尽快把当前 Work Memory 体系中最关键的风险显性化。
