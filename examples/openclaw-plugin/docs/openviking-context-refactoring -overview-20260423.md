# OpenViking 上下文架构重构方向

1. **Working Memory（工作记忆）**
2. **Tool Compression（工具结果压缩）**

## 目录

1. [Working Memory 重构方向](#working-memory-重构方向)
2. [工具压缩策略重构方向](#工具压缩策略重构方向)

## Working Memory 重构方向

### 一、重构目标

Working Memory 目标是把 archive 的 `.overview.md` 从一次性摘要，升级为一份**可持续维护的长期状态文档**。

目标形态是固定 7 段：

- `Session Title`：会话标题，定义主题边界
- `Current State`：当前状态，记录最新阶段、当前卡点和下一步
- `Task & Goals`：任务目标，记录会话目的和关键目标
- `Key Facts & Decisions`：关键事实与决策，记录稳定结论、约束、偏好、技术选择
- `Files & Context`：文件与上下文，记录后续回答依赖的路径与资源
- `Errors & Corrections`：错误与修正，记录失败尝试和纠偏结果
- `Open Issues`：未决事项，记录尚未解决的问题、风险和待办

### 二、核心设计

#### 1. archive 本体就是 Working Memory

不新增 sidecar 存储，直接复用 `archive_NNN/.overview.md`。

这样做的结果是：

- `assemble()` 继续消费 `latest_archive_overview`
- 不引入额外的数据通路
- `.overview.md` 从“归档说明”升级为“长期状态层”

#### 2. 更新协议采用 section-level op，而不是整篇重写

Working Memory 的更新不是“重新生成一整篇文档”，而是对每个 section 提交操作：

- `KEEP`
- `UPDATE`
- `APPEND`

这样服务端可以基于 section 语义做合并，而不是只能在整篇文本上做替换。

当前实现里，这条协议已经采用：

- `tool_call`
- `JSON schema`
- 服务端 `_merge_wm_sections()`

#### 3. 信息保留必须由服务端负责

如果只靠 prompt 告诉模型“不要丢信息”，长链路下仍会丢。

因此 WM 合并路径必须经过 Guards。当前需要保护的 section 主要有：

- `Session Title`：防止标题漂移
- `Key Facts & Decisions`：防止关键事实被过度压缩
- `Files & Context`：防止文件路径回退
- `Errors & Corrections`：强制 append-only
- `Open Issues`：防止未决项静默消失

LLM 只负责提出 section op，服务端负责决定哪些 op 可以被接受。

#### 4. 归档边界与 WM 更新边界解耦

当前设计里：

- `commit` Phase 1：先归档原始消息、切 recent tail、更新 `pending_tokens`
- `commit` Phase 2：再异步生成或更新 Working Memory

拆分策略：

- 归档边界可以先快速落盘
- WM 更新失败时，原始消息仍然已经进入 archive，可重试、可回退
- recent tail 由 `keep_recent_count` 承担，连续性不依赖 WM 写成功

### 三、当前实现作为基线

当前代码已经落地了这条线的大部分主骨架：

- 7 段 WM 已实现
- `tool_call + JSON schema` 已实现
- 5 个 Guards 已实现
- `pending_tokens + keep_recent_count` 已实现
- `afterTurn / compact / assemble` 已围绕 WM 打通

### 四、下一阶段优化重点

#### 1. “稳定摘要” -> “摘要 + 回查”

Working Memory 本质上仍然是摘要，摘要一定有损。下一步补齐按需回查 archive 原文的能力。

建议方向：

- `keyword`：跨 archive 按关键词回查
- `tail`：按最近归档边界回查

当前 `ov_archive_expand` 只覆盖“已知 archive_id 的单点展开”，还不是完整检索层。

#### 2. “按消息条数保留 recent tail” -> “按 token 预算保留”

当前 `keep_recent_count` 已经解决了归档后上下文断裂问题，但它按消息条数保留，控制精度不够。

下一步更合理的方向是：

- 以 token 预算而不是消息条数控制 recent tail
- 避免一条超长消息挤占整个 recent window

---

## 工具压缩策略重构方向(待实现)

### 一、重构目标

工具压缩策略是把 tool output 从 session 主消息流中结构化拆出去，openviking侧独立存储。

目标形态是两层：

#### 1. ToolResult Preview 层

保留：

- 工具身份，例如 `grep`、`cat`、`pytest`
- 状态，例如 `completed`、`error`、`interrupted`
- 适度 preview，例如只保留前 `2KB` 输出或一段摘要性片段
- 到全文的引用，例如 `tool_output_ref = ov://tool-results/session_id/{tool_call_id}`

用途：

- 支撑 assemble 的主上下文，例如让模型知道“刚才跑了 `pytest`，并且失败了”
- 控制 session 消息体积，例如 80KB 的 `cat` 输出不再直接进入主消息流
- 保证消息前缀稳定，例如同一条工具结果不会在不同轮次被反复重新截断

#### 2. Full Output 层

保留完整 tool output 原文，进入独立存储。

用途：

- 记忆抽取需要全文时读取，例如从完整编译日志里提取错误类型、文件路径和失败原因
- 追溯问题时按需读取，例如用户追问“刚才测试到底是哪几个 case 失败”
- 避免 tool 全文默认进入 prompt，例如 `ls -R`、`cat package-lock.json` 这类大输出不再默认占满上下文

### 二、核心设计

#### 1. 写入时截断，而不是读取时临时决定

最重要的原则是：**截断决策应在写入 session 时确定，而不是在 assemble 时动态决定。**

这样做的结果是：

- session 消息写入后形态稳定
- assemble 读到的是稳定前缀
- 更有利于 prompt cache

#### 2. 对模型用 preview，对系统保留全文

工具压缩的目标不是丢数据，而是把全文从默认上下文里移出去。

因此架构上要同时满足：

- 模型默认只看到 preview
- 系统在需要时仍能读全文

这也是为什么 `tool_output_ref` 是关键字段：它把“消息里展示什么”和“系统里保留什么”解耦了。

#### 3. 主消息流和细节读取路径解耦

只要工具全文仍然和 session 主消息绑在一起，就无法真正控制上下文体积。

建议的数据流是：

1. `afterTurn` 检查 `tool_output`
2. 小结果直接写入 `ToolPart.tool_output`
3. 大结果全文写入 `tool-results/session_id/{tool_call_id}.txt`
4. `ToolPart` 只保留 preview + `tool_output_ref`
5. `assemble` 默认只消费 preview
6. extraction 或后续 detail retrieval 通过 `tool_output_ref` 读全文

### 三、重构路径

#### 1. 第一阶段：建立 `tool-results + preview + ref`

这是主改造项：

- 服务端增加 `tool-results` 独立存储
- `ToolPart` 增加 `tool_output_ref`
- `afterTurn` 对超长输出只写 preview + ref
- extraction pipeline 通过 ref 读全文

这一阶段完成最关键的边界切分。

#### 2. 第二阶段：增加 `getSessionContext` 返回层截断能力

作用不是替代第一阶段，而是作为返回层兜底：

- 兼容旧 session
- 覆盖 fallback 场景
- 降低历史数据迁移成本

---

> 关联文档：
> - `2026-04-23-wm-v2-design-overview.md`
> - `tool-compression-strategy.md`
