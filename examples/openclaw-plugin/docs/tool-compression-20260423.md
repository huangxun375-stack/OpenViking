# OpenViking 工具结果压缩方案

## 一、问题

OV 插件的 afterTurn（写入）和 assemble（读取）对 tool_output 全量透传。一条 `cat` 输出 30KB+，20 轮后上下文 600KB+，远超模型窗口。

| 位置 | 当前行为 | 问题 |
|------|---------|------|
| `afterTurn` | tool_output 全文写入 OV Server | 存储膨胀，后续全量拉取 |
| `getSessionContext` | 返回全量 session 消息 | 网络传输大 |
| `buildSessionContext` | 超预算时 shift 整条消息 | 粒度太粗 |

## 二、核心设计

### 设计原则

**写入时截断，一次完成。** 对标 Claude Code 的 `persistToolResult`：工具执行后立即截断落盘，消息流中始终是 preview。OV 在 afterTurn 做同样的事。

```
Claude Code:    工具执行 → persistToolResult(全文写磁盘, 消息存preview) → 后续 turn 稳定
OpenViking:     工具执行 → afterTurn(全文写tool-results, session存preview+ref) → 后续 turn 稳定
```

这样做的好处：
1. **prompt cache 友好**：session 存储的内容写入后不变，assemble 每次拉到相同前缀
2. **记忆抽取不受影响**：全文在 tool-results 存储层，extraction pipeline 通过 ref 读全文
3. **全链路受益**：存储小、网络传输小、assemble 不需要额外处理

### 截断参数

| 参数 | 值 | 来源 |
|------|------|------|
| 截断阈值 | 50K chars | Claude Code `TOOL_OUTPUT_TRUNCATE_CHARS` |
| Preview 大小 | 2KB chars | Claude Code `PREVIEW_SIZE_BYTES=2000` |

### 数据流

```
OpenClaw 工具执行完成
        │
        ▼
afterTurn (context-engine.ts)
        │
        ├── tool_output ≤ 50K chars → 原样写入 session 消息
        │
        └── tool_output > 50K chars
                │
                ├─① storeToolOutput(session_id, tool_call_id, full_output)
                │     → Server 写入: tool-results/{tool_call_id}.txt
                │
                └─② addSessionMessage(session_id, role, [
                       { type: "tool",
                         tool_output: "[Truncated]\n{2KB preview}",
                         tool_output_ref: "ov://tool-results/..." }
                     ])
                     → session 消息始终是小的 ✓
                     → 后续 assemble 天然稳定 ✓
```

---

## 三、实施步骤

### Step 0：afterTurn 轻量清理

**做什么**：写入前去除 tool_output 中的噪声（不截断，不丢数据）。

**代码位置**：`context-engine.ts` afterTurn ovParts 构造处

```typescript
function cleanToolOutput(output: string): string {
  return output
    .replace(/\x1b\[[0-9;]*m/g, "")
    .replace(/\n{3,}/g, "\n\n")
    .replace(/[\x00-\x08\x0e-\x1f]/g, "");
}
```

**工作量**：~15 行，纯客户端  
**验证**：对比清理前后 session 消息大小  
**风险**：零

---

### Step 1：afterTurn 截断 + tool-results 存储（核心方案）

**做什么**：afterTurn 写入时，大 tool_output 全文存入 tool-results，session 消息只存 preview + ref。

#### 1.1 Server 端：tool-results 存储

**存储 API**：

```
POST /api/v1/tool-results/{session_id}/{tool_call_id}
  Content-Type: text/plain
  Body: <full tool output>

GET  /api/v1/tool-results/{session_id}/{tool_call_id}
  → 200 text/plain

DELETE /api/v1/tool-results/{session_id}
  → 清理该 session 下所有 tool results
```

**存储位置**：

```
agents/{agent_id}/sessions/{session_id}/
  messages.jsonl
  tool-results/
    {tool_call_id}.txt
```

**ToolPart 模型扩展**：

```python
class ToolPart(BasePart):
    type: str = "tool"
    tool_name: str = ""
    tool_input: Optional[dict] = None
    tool_output: str = ""
    tool_status: str = ""
    tool_output_ref: Optional[str] = None   # 新增
```

#### 1.2 客户端：client.ts 新增接口

```typescript
async storeToolOutput(
  sessionId: string,
  toolCallId: string,
  output: string,
  agentId?: string,
): Promise<{ uri: string; size: number }> {
  const url = `/api/v1/tool-results/${encodeURIComponent(sessionId)}/${encodeURIComponent(toolCallId)}`;
  return this.request(url, {
    method: "POST",
    body: output,
    headers: { "Content-Type": "text/plain" },
  }, agentId);
}
```

#### 1.3 客户端：afterTurn 改造

`context-engine.ts` 中 afterTurn 的 ovParts 构造改为：

```typescript
const TOOL_OUTPUT_TRUNCATE_THRESHOLD = 50_000;
const TOOL_OUTPUT_PREVIEW_SIZE = 2_048;

for (const part of msg.parts) {
  if (part.type !== "tool") { /* 原有逻辑 */ continue; }

  let toolOutput = cleanToolOutput(part.toolOutput);
  let toolOutputRef: string | undefined;

  if (toolOutput.length > TOOL_OUTPUT_TRUNCATE_THRESHOLD) {
    const toolCallId = part.toolCallId || `${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
    try {
      const stored = await client.storeToolOutput(OVSessionId, toolCallId, toolOutput, agentId);
      toolOutputRef = stored.uri;
      toolOutput =
        `[Truncated: ${toolOutput.length.toLocaleString()} → ${TOOL_OUTPUT_PREVIEW_SIZE.toLocaleString()} chars]\n` +
        `[Full output: ${stored.uri}]\n` +
        toolOutput.slice(0, TOOL_OUTPUT_PREVIEW_SIZE) + "\n...";
    } catch {
      // fallback: 简单截断，无 ref
      toolOutput =
        `[Truncated: ${toolOutput.length.toLocaleString()} chars]\n` +
        toolOutput.slice(0, TOOL_OUTPUT_PREVIEW_SIZE) + "\n...";
    }
  }

  ovParts.push({
    type: "tool" as const,
    tool_id: part.toolCallId,
    tool_name: part.toolName,
    tool_input: part.toolInput,
    tool_output: toolOutput,
    tool_status: part.toolStatus,
    ...(toolOutputRef ? { tool_output_ref: toolOutputRef } : {}),
  });
}
```

#### 1.4 Server 端：记忆抽取集成

extraction pipeline 处理带 `tool_output_ref` 的 ToolPart 时，从 tool-results 读全文：

```python
async def resolve_tool_output(part: ToolPart) -> str:
    if part.tool_output_ref:
        full = await tool_results_store.read(part.tool_output_ref)
        return full if full else part.tool_output
    return part.tool_output
```

#### 1.5 回退安全性

| 失败点 | 回退行为 |
|--------|---------|
| `storeToolOutput` 调用失败 | fallback 到简单截断（无 ref） |
| extraction 读 ref 失败 | 用 session 中的 preview 做抽取 |
| 存储空间不足 | GC + fallback 到简单截断 |

**工作量**：~60 行客户端 + Server 端 API + extraction 集成  
**验证**：
1. 触发大 tool_output → 确认 tool-results 有文件，session 消息是 preview+ref
2. 连续几轮 assemble → 确认前缀不变（prompt cache 可命中）
3. 记忆抽取 → 确认通过 ref 读到全文

**预期收益**：token -20~30%，前缀稳定，记忆抽取质量不受影响

---

### Step 2：getSessionContext 截断参数（兜底防御层）

**做什么**：给 getSessionContext API 加截断参数，作为旧数据和 fallback 场景的兜底。

**为什么需要**：Step 1 只对新写入的消息有效。已有的旧 session 中可能存在未截断的大 tool_output。getSessionContext 截断参数可以覆盖这些场景。

#### 2.1 Server 端

```
GET /sessions/{id}/context?token_budget=128000&tool_output_max_chars=2048
```

Server 返回前对超阈值的 `tool_output` 做 slice，原始存储不变。

#### 2.2 客户端

```typescript
// client.ts
async getSessionContext(
  sessionId: string,
  tokenBudget: number,
  agentId?: string,
  toolOutputMaxChars?: number,
): Promise<SessionContext> { /* ... */ }

// context-engine.ts assemble 调用时
const ctx = await client.getSessionContext(ovSessionId, tokenBudget, agentId, TOOL_OUTPUT_PREVIEW_SIZE);
```

**工作量**：~25 行客户端 + Server 端  
**验证**：用旧 session（含大 tool_output）调用，确认返回截断版  
**风险**：零——不改存储

---

### Step 3：assemble 消息分层  (**这里再进行重组和裁剪会让缓存不稳定，待定**)

**做什么**：在 `buildSessionContext` 中按时间远近分层处理消息。

**为什么**：即使 Step 1 截断了大 output，大量中间轮次的小消息仍占空间。

#### 分层规则

| 层 | 消息范围 | 处理 |
|----|---------|------|
| 最近层 | 尾部 2~4 轮 | 完整保留 |
| 中间层 | 其余活跃消息 | tool 内容压缩为骨架（tool_name + status） |
| 丢弃层 | 超出 budget | 丢弃（依赖 archive overview） |

#### 代码

```typescript
const RECENT_MESSAGES_KEEP = 8;

const recent = messages.slice(-RECENT_MESSAGES_KEEP);
const older = messages.slice(0, -RECENT_MESSAGES_KEEP);
const compressed = older.map((msg) => compressToolResults(msg));
const all = [...compressed, ...recent];
```

**注意**：消息分层会改变旧消息内容 → 前缀不稳定。如果 prompt cache 优先级高，可以考虑将分层结果也在 afterTurn 时确定（写入 session 时标记哪些消息可压缩），而不是每次 assemble 动态计算。这是未来可优化的方向。

**工作量**：~70 行  
**验证**：20+ 轮会话，确认中间轮只保留骨架  
**预期收益**：额外 -10~15% token

---

### Step 4：溢出保护

**做什么**：当 Step 1~3 仍不够时的兜底。

#### 4.1 准确报告 estimatedTokens

```typescript
return { messages: sanitized, estimatedTokens: assembledTokens };
```

#### 4.2 主动触发 OV commit

```typescript
if (assembledTokens > tokenBudget * 0.9) {
  await doCommitOVSession(sessionId, sessionKey);
}
```

#### 4.3 熔断保护

连续 N 次 commit 失败后停止重试。

**工作量**：~30 行，可穿插在任何阶段  

---

### Step 5：GC 策略

| 事件 | tool-results 处理 |
|------|------------------|
| session 活跃 | 保留 |
| commitSession 归档 | 保留（抽取可能还需要） |
| 记忆抽取完成 | 标记可清理 |
| session 过期 | 随 session 删除 |

```python
async def gc_tool_results():
    for session_dir in list_sessions():
        if session_expired(session_dir) or extraction_completed(session_dir):
            shutil.rmtree(session_dir / "tool-results", ignore_errors=True)
```

---

## 四、prompt cache 稳定性分析

OV 的消息流是 append-only：

```
Turn 1: assemble → [archive, msg1, msg2, toolA]
Turn 2: assemble → [archive, msg1, msg2, toolA, msg3]    ← 前缀应不变
Turn 3: assemble → [archive, msg1, msg2, toolA, msg3, msg4]
```

如果前缀 bit-identical，OpenClaw 发给 API 时可命中 prompt cache。

### 稳定性因素

| 因素 | 是否稳定 | 说明 |
|------|---------|------|
| session 消息内容 | ✅ 稳定（Step 1 后） | afterTurn 写入即最终版，不再变化 |
| archive memory | ❌ 不稳定 | commit 后 overview/preAbstracts 变化 |
| budget 变化 | ❌ 不稳定 | tokenBudget 变化导致不同的 shift 行为 |
| 消息分层（Step 3） | ❌ 不稳定 | 分层边界随新消息移动 |
| `sanitizeToolUseResultPairing` | ✅ 稳定 | 幂等操作 |

**结论**：Step 1（afterTurn 写入时截断）解决了最大的稳定性问题——tool_output 内容不变。其余因素（archive、budget、分层）是 OV assemble 架构本身的特性，不在本方案范围内。

### 与 Claude Code 的对比

Claude Code 用 `ContentReplacementState` 冻结截断决策，是因为它在每个 turn 动态决定是否截断同一条消息。OV 不需要这个机制，因为 afterTurn 在写入时就完成了截断——**写入即冻结**。

---

## 五、与 Claude Code 的对照

| 维度 | Claude Code | OV 方案 |
|------|------------|---------|
| 截断时机 | 工具执行后立即（`persistToolResult`） | afterTurn 写入时 |
| 全文存储 | 本地磁盘 `tool-results/` | OV Server `tool-results/` |
| 消息中引用 | `<persisted-output>...path...</persisted-output>` | `[Full output: ov://tool-results/...]` |
| Preview 大小 | 2KB | 2KB |
| 读回方式 | 模型用 Read 工具读磁盘 | ov_archive_expand 或新增读回工具 |
| 记忆抽取 | 无 | extraction 通过 `tool_output_ref` 读全文 |
| 决策冻结 | `ContentReplacementState` | 不需要——写入即冻结 |
| prompt cache | 冻结保证 bit-identical | 写入时确定内容保证 bit-identical |

**OV 不需要照搬的机制**：
- `ContentReplacementState`：写入即冻结，不需要额外缓存
- `cachedMicrocompactPath`（cache_edits）：Anthropic API 专属
- History Snip：OV archiving 天然覆盖
- Context Collapse：OV archive overview + index 已实现

**OV 的架构优势**：
- Server 端记忆抽取用全文，客户端用截断版——两全
- commitSession 在 Server 端做，不消耗客户端 token

---

## 六、代码修改速查

| 文件 | 改动 | Step |
|------|------|:----:|
| `context-engine.ts` afterTurn | cleanToolOutput 去噪 | 0 |
| OV Server API | tool-results 存储 POST/GET/DELETE | 1 |
| OV Server Model | ToolPart 增加 tool_output_ref | 1 |
| `client.ts` | 新增 storeToolOutput | 1 |
| `context-engine.ts:984` | afterTurn 大输出截断+存储 | 1 |
| OV Server extraction | resolve_tool_output 集成 | 1 |
| OV Server API | getSessionContext 增加 toolOutputMaxChars | 2 |
| `client.ts` | getSessionContext 传参 | 2 |
| `context-engine.ts:822` | assemble 调用传参 | 2 |
| `context-engine.ts:509` | buildSessionContext 消息分层 | 3 |
| `context-engine.ts` assemble | estimatedTokens + 主动 commit | 4 |

---

## 七、实施路径

```
Step 0 (~15行)         Step 1 (核心方案)            Step 2 (兜底)             Step 3 (~70行)          Step 4 (~30行)
┌───────────────┐    ┌───────────────────┐    ┌───────────────────┐    ┌───────────────────┐    ┌───────────────────┐
│ afterTurn     │    │ afterTurn 截断    │    │ getSessionContext │    │ assemble 消息分层  │    │ 溢出保护          │
│ 轻量清理       │ →  │ + tool-results    │ →  │ 截断参数（兜底）   │ →  │ 最近/中间/早期     │ →  │ 主动 commit       │
│               │    │ + extraction 集成  │    │ 覆盖旧数据        │    │                   │    │ 熔断保护          │
└───────────────┘    └───────────────────┘    └───────────────────┘    └───────────────────┘    └───────────────────┘
  零风险去噪           token -20~30%             旧session兜底            额外 -10~15%            极端场景保护
                       前缀稳定                  零风险
                       记忆抽取不受影响
```

每个 Step 可独立实现验证。Step 1 是核心，其余为辅助。
