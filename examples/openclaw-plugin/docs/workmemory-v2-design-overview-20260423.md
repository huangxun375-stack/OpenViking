# OpenViking Working Memory v2 — 设计文档

## 文档目标

OpenViking Working Memory v2 的设计方案与当前实现状态。本文回答：

1. 旧版 archive 产物为什么不够用，WM v2 要解决什么
2. 当前 OpenViking + OpenClaw 插件已经有什么能力，还缺什么
3. WM v2 选了什么方案，为什么这样选
4. 效果如何

本文不展开 `user/agent memories` 这类长期记忆系统和 `autoRecall` 向量召回机制——它们只在必要处作为边界说明出现。

---

## 需求与实现状态

### afterTurn（每轮对话后自动归档）

| 需求 | 状态 | 说明 |
|---|---|---|
| 自动检测归档时机 | ✅ 已实现 | pending_tokens 滑动窗口，O(1) 计算 |
| 增量更新 WM | ✅ 已实现 | tool_call + JSON schema + 服务端 Guards |
| 归档后保留最近消息 | ✅ 已实现 | keep_recent_count，保持上下文连贯 |
| 按 tokens 保留最近消息 | ❌ 未实现 | 当前按消息条数，精度不够 |

### compact（主动上下文压缩）

| 需求 | 状态 | 说明 |
|---|---|---|
| 全量重写 WM 并归档 | ✅ 已实现 | keep_recent_count=0，彻底压缩 |
| WM-based 直拼（不调 LLM） | ❌ 已设计未实现 | WM 已是最新时直接拼上下文返回 |
| session active 自动 trim | ❌ 未实现 | 消息单调增长问题的治本方案 |

### assemble（构建 LLM 上下文）

| 需求 | 状态 | 说明 |
|---|---|---|
| WM overview 作为会话摘要 | ✅ 已实现 | 结构化 7 段模板 + 旧格式自动升级：
`Session Title`（会话标题，概括主题）、
`Current State`（当前状态，概括最新进展和下一步）、
`Task & Goals`（任务目标，说明会话目的和关键目标）、
`Key Facts & Decisions`（关键事实与决策，记录稳定结论/约束/选择）、
`Files & Context`（文件与上下文，记录后续回答依赖的路径/资源）、
`Errors & Corrections`（错误与修正，记录失败尝试和纠偏）、
`Open Issues`（未决事项，记录尚未解决的问题/风险/待跟进项） |

| 按 archive_id 展开归档原文（`ov_archive_expand`） | ✅ 已实现 | 通过工具/API 读取单个 completed archive 的原始消息 |
| 归档对话关键词回查（`ov_archive_search`） | ❌ 已设计未实现 | 当前没有按关键词/尾部窗口搜索 archive 原文的能力 |

### 通用

| 需求 | 状态 | 说明 |
|---|---|---|
| 信息保留 Guards | ✅ 已实现 | 5 个段级保护函数 |
| 累积型段主动合并 | ❌ 未实现 | 当前依赖 LLM 被动发起合并 |

---

## 一、背景

### 1.1 核心问题

OpenViking 的归档（commit）流程在每次 commit 时生成 session overview。旧版（`compression.structured_summary` v1）采用**全量重写**策略——LLM 每次从当前归档消息从零生成新 overview，前一个 archive 的 overview 只作为参考传入（可选）。

这导致一个严重的工程问题：**信息逐层丢失**。

具体来说，假设用户经历了 archive_001 → 002 → 003 → 004 四次归档：

- archive_001 的 overview 准确记录了"Caroline 在 2023-05-07 参加了 LGBTQ support group"
- archive_002 重写 overview 时，LLM 把这条事实浓缩成了"Caroline 参加过社群活动"
- archive_003 重写时，这条事实进一步模糊为"Caroline 有社群参与经历"
- archive_004 重写时，这条事实**完全消失**

在 LoCoMo 评测中，这个问题表现为：

- Main（旧版）在 35 题小样本上准确率 60%（可接受）
- 但在 19 sessions / 152 题的大样本上**崩到 15.13%**（Cat2 推理型仅 5.41%）
- 相当于归档 20 次之后，early session 的事实几乎全丢

关键物证是 Q1 手测：Main 版 archive_004 的 Historical Context 里已经没有 "support group on 7 May 2023" 这条事实，LLM 只能答 "no mention of support group"。

### 1.2 Claude Code 是怎么做的

Claude Code 的 `session memory` 不是一个通用记忆框架，而是一份"当前会话的工作笔记"——固定 10 段结构，由后台 forked agent 用 Edit 工具多轮增量编辑。

它的核心机制：

- `transcript`（jsonl）是真相源，`summary.md` 是派生的工作摘要
- 每个 session 一份 `summary.md`，持续增量修订
- 只在主 REPL 线程更新（不被 subagent 污染）
- 通过 `lastSummarizedMessageId` 标记覆盖边界
- compact 时直接用 `summary + recent tail` 拼上下文，不再调 LLM
- resume 时用 boundary metadata 重建消息链

关键设计原则：

- 工作记忆必须是固定结构、可持续修订的文档，而不是自由摘要
- 信息不变的段落直接保留（Edit 工具天然不碰没改的部分）
- `Current State` 每次必须更新，这是 compact 后恢复连续性的核心

关键参数（源码路径：`src/services/SessionMemory/`）：

- `MAX_SECTION_LENGTH = 2000`（单段上限）
- `MAX_TOTAL_LENGTH = 12000`（总上限）
- `minimumMessageTokensToInit = 10000`（首次生成阈值）
- `minimumTokensBetweenUpdate = 5000`（更新间隔）
- `toolCallsBetweenUpdates = 3`

### 1.3 OV 当前已经有什么

在 WM v2 之前，OpenViking + OpenClaw 插件已经具备 3 个重要基础能力；而在当前实现中，这 3 条链路都已经升级到可用状态：

**1. `assemble()` 已经在消费 archive 产物**

`assemble()` 调用 `getSessionContext()`，把 OV 返回的 session context 重组为摘要 + 活跃上下文：

- `latest_archive_overview` → `[Session History Summary]`
- `pre_archive_abstracts` → **保留兼容字段，但当前固定为空数组，不再组装 `[Archive Index]`**
- `messages` → active messages；这里包含服务端拼回的“未完成 archive 的 pending messages + 当前 live messages”

并附带 `systemPromptAddition` 告诉模型：

- `[Session History Summary]` 是有损压缩摘要
- active messages 比 summary 更新，冲突时优先相信 active messages
- 缺少细节时应询问用户，而不是猜测

当前提示词**没有**引导模型调用 `ov_archive_search`。

对应实现：`examples/openclaw-plugin/context-engine.ts` + `openviking/session/session.py`

**2. `afterTurn()` 已经在做原始消息落盘和异步 commit**

- 用 `prePromptMessageCount` 切出本轮新增消息
- 提取结构化的 text / tool parts
- 清理注入噪音（`<relevant-memories>`）
- 追加写入 OV session
- `pending_tokens >= commitTokenThreshold` 时触发 `commit(wait=false)`

对应实现：`examples/openclaw-plugin/context-engine.ts`

**3. `compact()` 已经在做同步 commit 和 summary 回读**

- `commit(wait=true)` 同步等待
- 再回读 `getSessionContext()` 取最新 overview
- 返回 `tokensBefore / tokensAfter / summary`

对应实现：`examples/openclaw-plugin/context-engine.ts`

这说明 OV 当前已经是一个 `archive-aware context engine`。当前实现缺的不是从头建一套新系统，而是继续补齐“工作记忆之外的归档细节回查能力”。

### 1.4 对照 CC 之后，当前还缺什么

| 缺口 | 说明 |
|---|---|
| **按关键词/语义回查 archive 原文未实现** | 当前只有 `ov_archive_expand`，必须指定 `archive_id` 才能展开；没有 `keyword` / `tail` 搜索接口 |
| **`assemble()` 不再提供 Archive Index** | `pre_archive_abstracts` 字段保留但固定返回空数组，模型无法直接看到 archive 列表 |
| **WM-based compact 直拼未实现** | compact 仍走 `commit(wait=true)` + `getSessionContext()` 回读 |
| **session active 自动 trim 未实现** | 当前仍依赖 commit 边界控制 active 消息增长 |

---

## 二、设计方案

### 2.1 设计原则

WM v2 的设计遵循 3 条核心原则：

**原则 1：archive 本体就是 working memory，用固定的结构化模板承载**

不在 archive 旁边再造一份独立 sidecar，而是把 `.overview.md` 从自由格式的摘要升级为**固定 7 段结构化模板**（Session Title / Current State / Task & Goals / Key Facts & Decisions / Files & Context / Errors & Corrections / Open Issues）。每段有明确的职责，LLM 不能随意增删段落。

有结构才能做增量更新——LLM 对每个段独立发 `KEEP` / `UPDATE` / `APPEND` 操作，未变化的段发 `KEEP` 由服务端原样复制（零 token 消耗、零信息丢失），变化的段走校验后合并。

- `assemble()` 消费的仍然是 `latest_archive_overview`，无需新增数据通路
- `getSessionContext()` 返回的字段不变，向后兼容
- 存储结构（`archive_NNN/.overview.md`）不变

**原则 2：信息保留是系统责任，不是 LLM 责任**

在 prompt 里叮嘱 LLM "不要丢信息" 是没用的——实验证明无论怎么改措辞，丢失量不变。

所以我们把职责分开：**LLM 只负责"判断变了什么"，服务端 guard 函数负责"保证不丢信息"**。具体机制见 §2.4 服务端 Guards。

**原则 3：向后兼容与平滑升级**

- **未开启 WM 的会话**：不配置新字段时行为完全不变，`keep_recent_count=0` 等价于旧版全量归档
- **已有旧格式 overview 的会话**：服务端自动检测 overview 是否包含 WM 7 段 header。如果是旧版自由格式，走创建路径（全量生成 7 段 WM），不走 tool_call 增量更新——下一次 commit 时自动完成格式升级，无需手动迁移

### 2.2 WM 数据结构

WM 是一份 Markdown 文档，固定 7 个 section，顺序不变：

```markdown
# Working Memory

## Session Title
_简短独特的 5-10 词标题，信息密集_

## Current State
_当前工作状态、待完成任务、下一步_

## Task & Goals
_用户目标、关键设计决策、解释性上下文_

## Key Facts & Decisions
_重要结论、技术选择及理由、用户偏好与约束_

## Files & Context
_重要文件/模块及路径_

## Errors & Corrections
_遇到的错误及修复、用户纠正、失败方案_

## Open Issues
_未解决问题、阻塞项、后续风险_
```

相比 CC 的 10 段模板，我们去掉了 3 个段：

- `Key Results`：与 Current State 功能重叠，不需要单独一段记录结果
- `Worklog`：Current State + Key Facts 已经提取了会话中有用的信息子集，再加一段 Worklog 是重复记录
- `Codebase and System Documentation`：CC 面向 coding 场景有大量代码库上下文，OV 是通用对话助手，Files & Context 段已涵盖此需求

每段上限 ~2000 tokens，总 WM 上限 ~12000 tokens（prompt 指引，对齐 CC 的预算量级）。服务端 guard 在单段 ≥25 bullets 或 ≥1500 tokens 时触发 consolidation 提醒。

### 2.3 增量更新协议

WM 更新通过 **tool_call（function calling）+ JSON schema** 实现：LLM 调用 `update_working_memory` 工具，以结构化 JSON 提交对 7 个段的逐段操作。

**为什么用 tool_call 而不是文本标记**：文本标记（`[KEEP]/[UPDATE]/[APPEND]`）依赖 LLM 格式一致性，格式漂移时直接崩溃。tool_call 有 JSON schema 强约束，LLM 输出必须符合 schema 才会被接受——漏段、多段、格式错误在 schema 层直接拦截。

**为什么不用 Agent Loop**：CC 用 forked agent + Edit 工具实现增量编辑，鲁棒性最高，但 OV 没有 ForkedAgent 基础设施，从零搭建 ROI 不够。tool_call 方案复用现有 VLM function calling 能力，约 100 行代码实现。

#### tool schema 定义

```python
WM_SEVEN_SECTIONS = [
    "Session Title", "Current State", "Task & Goals",
    "Key Facts & Decisions", "Files & Context",
    "Errors & Corrections", "Open Issues",
]

WM_UPDATE_TOOL = {
    "type": "function",
    "function": {
        "name": "update_working_memory",
        "parameters": {
            "type": "object",
            "required": ["sections"],
            "additionalProperties": False,
            "properties": {
                "sections": {
                    "type": "object",
                    "required": WM_SEVEN_SECTIONS,      # 7 段全部必填
                    "additionalProperties": False,
                    "properties": {name: SECTION_OP_SCHEMA
                                   for name in WM_SEVEN_SECTIONS},
                }
            },
        },
    },
}
```

每段的操作（`SECTION_OP_SCHEMA`）用 `oneOf` 约束为三种形状之一：

- `{"op": "KEEP"}` — 原样保留
- `{"op": "UPDATE", "content": "..."}` — 全段替换
- `{"op": "APPEND", "items": ["...", "..."]}` — 追加条目

op 字段使用 `"type": "string", "enum": ["KEEP"]` 形式（而非 `"const"`），兼容更多 JSON Schema 版本。`additionalProperties: false` + `required` 把 LLM 输出严格钉在这个 schema 里，不接受其他任何格式。

#### 段级合并

服务端 `_merge_wm_sections(old_wm, ops)` 按 `WM_SEVEN_SECTIONS` 常量遍历 7 段：

- **KEEP** → 原样复制旧内容（零 token、零丢失）
- **UPDATE** → 用 LLM 提供的 content 替换
- **APPEND** → 旧内容 + LLM 提供的 items（渲染为 `- item`）
- 漏段 / 未知 op → 兜底 KEEP

关键实现：`session.py: _merge_wm_sections()` + `_parse_wm_sections()`

### 2.4 服务端 Guards

LLM 在更新 WM 时会出现两类问题：一是在合并精简时"顺手"丢掉旧事实（特别是早期对话中的信息），二是在格式漂移时产生不合预期的 UPDATE 操作。Prompt 无法解决这些问题（见原则 2）。

Guards 是服务端在合并 LLM 提交的操作时，按段执行的语义校验函数。核心思想：**即使 LLM 说 UPDATE，服务端也根据段的特性决定是否接受**。

7 个段的保护策略：

| 段 | 数据特点 | Guard | 规则 |
|---|---|---|---|
| Session Title | **锚定型**：会话身份标识，不应随意变更 | `_wm_enforce_title_stability` | UPDATE 与旧 title meaningful-word overlap < 1 → 回退 KEEP |
| Current State | **易变型**：每轮反映当前状态，需全量覆盖 | 无（刻意不加） | LLM 可自由 UPDATE |
| Task & Goals | **易变型**：目标随会话推进自然变化 | 无（刻意不加） | LLM 可自由 UPDATE |
| Key Facts & Decisions | **累积型**：重要结论不断积累，丢失代价高 | `_wm_enforce_key_facts_consolidation` | 双阈值验证：bullet count ≥ 旧 15% 且 lexical anchor coverage ≥ 70%。被拒时提取新 items 做 APPEND |
| Files & Context | **引用型**：文件路径一旦提及不应消失 | `_wm_enforce_files_no_regression` | UPDATE 丢失旧路径 → KEEP + APPEND 新路径 |
| Errors & Corrections | **只增型**：错误记录只增不删，避免重复踩坑 | `_wm_enforce_append_only` | UPDATE 降级为 APPEND，去重后只追加新条目 |
| Open Issues | **跟踪型**：未解决项不应被静默丢弃 | `_wm_enforce_open_issues_resolved` | silently drop 的 item → 加 `[restored]` 标签恢复 |

Key Facts 与 Errors 的区别：Errors 是纯 append-only（UPDATE 总被降级为 APPEND），而 Key Facts 允许"受控合并"——LLM 提交的合并 UPDATE 通过双阈值验证后可被接受（实测 22KB → 6.5KB）。

关键实现：`session.py: _wm_enforce_*()` 5 个函数

### 2.5 滑动窗口与 pending_tokens

插件频繁轮询 `GET /sessions/{id}` 获取 `pending_tokens`，必须高效计算。

方案：O(1) 滑动窗口。

- `SessionMeta` 新增 `pending_tokens: int` 和 `keep_recent_count: int`，持久化到 `.meta.json`
- `add_message` 时：新消息进入保留窗口尾部，窗口头部被挤出的消息 token 累加到 `pending_tokens`
- `commit` 时 `pending_tokens` 归零
- `GET /sessions/{id}` 直接读 meta，O(1)

关键实现：`session.py: add_message()` + `SessionMeta`

### 2.6 保留最近消息

commit 归档时不全量清空消息，保留最近 N 条维持上下文连贯。

- 参数 `keep_recent_count` 由插件在 commit API body 中传入
- afterTurn 路径默认 10，compact 路径硬编码 0
- OV 存储模型天然保证 tool_use/tool_result 配对完整性（ToolPart 自包含）

为什么 afterTurn 保留 10 条、compact 保留 0 条？

- afterTurn 是后台慢写，下一轮 LLM 仍需要看到对话尾部
- compact 意味着 token 预算已紧张，此时只有 WM + 新消息才合理

关键实现：`session.py: commit_async(keep_recent_count)`、`routers/sessions.py: CommitRequest`、`context-engine.ts`、`client.ts`

---

## 三、流程详解

### 3.1 afterTurn 流程

插件端不变，改动集中在服务端 commit 逻辑：

```
[插件] afterTurn
  ├── extractNewTurnMessages → 提取新消息（不变）
  ├── addSessionMessage → 逐条 POST /sessions/{id}/messages
  │     服务端: append msg + 滑动窗口更新 pending_tokens + save meta
  ├── GET /sessions/{id} → 返回 pending_tokens（O(1)）
  └── pending_tokens >= commitTokenThreshold?
        │
        YES → commitSession(wait=false, keepRecentCount=cfg.commitKeepRecentCount)
              │
              [服务端 commit_async]
              │
              ├── Phase 1（同步，不阻塞返回）
              │    ├── split_idx = total - keep_recent_count
              │    ├── 归档 messages[:split_idx] → archive_NNN/
              │    ├── 保留 messages[split_idx:]
              │    └── pending_tokens = 0, 更新 meta
              │
              └── Phase 2（asyncio.create_task 后台执行）
                   ├── 读旧 WM: _get_latest_completed_archive_overview()
                   ├── 有旧 WM?
                   │   YES → ov_wm_v2_update prompt + tool_call
                   │          → guards 检查每段决策
                   │          → _merge_wm_sections 段级合并
                   │   NO  → ov_wm_v2 prompt 全量创建
                   ├── 写入 archive_NNN/.overview.md + .abstract.md
                   ├── 提取 memory（不变）
                   └── 写入 .done
```

Phase 2 的关键细节：

- **格式检测**：读取旧 overview 后，先检查是否包含 WM 7 段 header（`any(f"## {s}" in overview for s in WM_SEVEN_SECTIONS)`）。如果是 legacy 格式（旧版 structured_summary），仍走创建路径全量生成 WM，而非 tool_call 更新——确保平滑升级
- **Section reminders**：更新路径中，`_build_wm_section_reminders()` 从旧 WM 提取每段的当前状态摘要，注入到 update prompt 的 `wm_section_reminders` 变量中，帮助 LLM 更好地判断哪些段需要变更
- **完整回退链**：tool_call 缺失 → `_fallback_generate_wm_creation` 重跑（传入旧 WM 作为上下文）；JSON parse 失败 → 正则 recovery → 段级 guard 兜底 KEEP；VLM 不可用 → 占位 summary

### 3.2 compact 流程

本版 compact 只改传参：

```
[插件] compact
  └── commitSession(wait=true, keepRecentCount=0)
        ├── Phase 1: 全部消息 → archive, messages.clear()
        ├── Phase 2: 读旧 WM → 创建/更新 → 写入
        └── 返回 → getSessionContext → 回读最新 WM

  相比旧版唯一的代码差异：显式传 keepRecentCount=0
```

WM-based compact 直拼（对应 CC 的 `trySessionMemoryCompaction`——WM 已是最新时直接拼上下文返回、不调 LLM）已设计但暂不实施，见需求表。

### 3.3 assemble（上下文组装）

当前实现保留 instruction / archive / session 三分区，但内容已经和早期设计稿不同：

```
┌──────────── System Prompt ────────────────────┐
│ systemPromptAddition:                          │
│   "## Session Context Guide                    │
│    1. [Session History Summary] 是压缩摘要      │
│    2. Active messages 是最新未压缩上下文        │
│    3. 二者冲突时优先 active messages            │
│    4. 缺细节时询问用户，不要猜"                 │
│ + 原始 system prompt                           │
└────────────────────────────────────────────────┘

┌──── Layer 1: Archive Memory (≤8K tokens) ─────┐
│                                                │
│  [user] [Session History Summary]              │
│  # Working Memory                              │
│  ## Session Title                              │
│  ## Current State                              │
│  ## Task & Goals                               │
│  ## Key Facts & Decisions                      │
│  ## Files & Context                            │
│  ## Errors & Corrections                       │
│  ## Open Issues                                │
└────────────────────────────────────────────────┘

┌──── Layer 2: Session Context ─────────────────┐
│  server 侧合并后的 ctx.messages:               │
│  - 未完成 archive 的 pending messages          │
│  - 当前 live session messages                  │
└────────────────────────────────────────────────┘

┌──── Layer 3: Reserved (≥20K tokens) ──────────┐
│  LLM 回复空间                                  │
└────────────────────────────────────────────────┘
```

实现上的几个关键点：

- `pre_archive_abstracts` 字段仍保留在 API 中，但当前固定返回空数组，仅用于兼容旧调用方
- 插件侧 `buildArchiveMemory()` 当前只消费 `latest_archive_overview`，不再构造 `[Archive Index]`
- `buildSystemPromptAddition()` 当前没有 `Archive Hint`，也没有提示模型调用 archive-search 工具
- 如果需要具体 archive 原文，当前只能通过独立工具 `ov_archive_expand` 按 `archive_id` 展开

---

## 四、效果验证

结构化 WM overview （关闭 autoRecall，152 题 LoCoMo sample0评测）：

| Version | Accuracy | Δ vs Main 同条件 | QA Input |
|---------|----------|------------------|----------|
| MP（Main, no recall） | 15.13% | — | 273448 |
| AP（WM, no recall） | 46.71% | +31.58pp | 380622 |

WM overview 独立贡献 +31.58pp

详细实验数据见各实验 `result.md`。


结构化 WM overview （开启 autoRecall，152 题 LoCoMo sample0评测）：
| Version | Accuracy | QA Input | QA Cached | QA Input+Cached |
|---------|----------|----------|-----------|-----------------|
| WM (076bc9fd) | 126/152 (82.9%) | 1,816,225 | 6,689,168 | 8,505,393 |
| Main (31628cd0) | 134/152 (88.2%) | 1,462,035 | 8,320,272 | 9,782,307 |
| Delta | -5.3 pp | +354,190 (+24.2%) | -1,631,104 (-19.6%) | -1,276,914 (-13.1%) |

---

## 五、归档对话回查（ov_archive_expand）— 当前已实现 / `ov_archive_search` 仍未实现

### 背景

WM overview 是有损的——长对话中的细粒度事实（精确数字、原话措辞、工具输出片段）无法全部保留在 overview 里。AP 组 46.71% 的准确率说明纯 overview 不够。CC 解决这个问题的方式是让模型通过 `transcriptPath` + grep/read 回溯原始对话记录。

OV 的归档消息存储在 `archive_NNN/messages.jsonl` 中。当前实现已经通过服务端 API + 插件工具把**单个 archive 的原始消息展开能力**暴露给模型，但还没有按关键词/尾部窗口做跨 archive 搜索。

### 当前实现

当前已经落地的是：

- 服务端 API：`GET /sessions/{session_id}/archives/{archive_id}`
- 插件工具：`ov_archive_expand`
- 返回内容：指定 completed archive 的 `abstract`、`overview` 和原始 `messages`

当前工具的工作方式是：

- 模型或调用方必须提供明确的 `archiveId`
- 插件调用服务端 archive detail API
- 工具把该 archive 的原始消息按 faithful message 形式重新展开并返回

这意味着当前回查能力是**按 archive_id 的定点展开**，而不是“从所有 archive 中按关键词搜索”。

### 当前限制

- 没有 `keyword` 模式：无法跨所有 archive 直接搜人名、日期、路径、专有名词
- 没有 `tail` 模式：无法直接读取“最近刚归档”的尾部窗口
- `assemble()` 不再提供 `[Archive Index]`，所以模型默认也看不到 archive 列表
- 当前实现返回的是单个 archive 的完整原始消息，不是经过上限裁剪的搜索结果窗口

### 后续可扩展方向

如果未来继续做 `ov_archive_search`，更合理的定位是补齐下面两种能力：

- `keyword`：跨 archive 搜索命中消息 + 上下文窗口
- `tail`：读取最近归档边界附近的消息窗口

它应该是对 `ov_archive_expand` 的补充，而不是替代。

---

## 附录 A：Claude Code 对比

| 维度 | Claude Code | OV WM v2 |
|---|---|---|
| WM 更新方式 | 子代理 + Edit 工具多轮增量编辑 | 单轮 tool_call + JSON schema + 服务端合并 |
| 更新频率 | 每 5K tokens + 3 次工具调用 | 仅 commit 时（pending_tokens ≥ threshold） |
| LLM 调用次数 | 多轮（每次 Edit 一轮） | 单轮 |
| Compact 时 | 直接用 SM 内容拼上下文，不调 LLM | 本期仍走 commit；直拼待实施 |
| 未变化内容 | 不调 Edit → 自动保留 | `{"op":"KEEP"}` → 服务端原样复制 |
| WM 结构 | 10 section | 7 section |
| 长度限制 | 12K 总体 + 2K/section | 12K 总体 + 2K/section（prompt 指引） |
| 回溯能力 | transcript path + grep/read | `ov_archive_expand`（已实现，按 `archive_id` 展开）；`ov_archive_search` 仍未实现 |
| 鲁棒性 | Edit 工具协议 | JSON schema + guards + recovery |

---

> **创建**：2026-04-23  
> **关联**：`specs/DECISIONS.md`、`timeline.md`、各实验 `result.md`  
> **参考**：[Claude Code Session Memory 调研与 OpenViking 工作记忆设计 v1](https://github.com/Mijamind719/OpenViking/commit/f41b940)
