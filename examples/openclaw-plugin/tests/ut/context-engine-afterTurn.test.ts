import { describe, expect, it, vi } from "vitest";

import type { OpenVikingClient } from "../../client.js";
import { memoryOpenVikingConfigSchema } from "../../config.js";
import {
  capturedMessageSignature,
  createMemoryOpenVikingContextEngine,
} from "../../context-engine.js";

function makeLogger() {
  return {
    info: vi.fn(),
    warn: vi.fn(),
    error: vi.fn(),
  };
}

function makeEngine(opts?: {
  autoCapture?: boolean;
  commitTokenThreshold?: number;
  getSession?: Record<string, unknown>;
  addSessionMessageError?: Error;
  cfgOverrides?: Record<string, unknown>;
  messageTail?: Array<{
    id: string;
    role: string;
    parts: Array<Record<string, unknown>>;
    created_at: string;
  }>;
}) {
  const cfg = memoryOpenVikingConfigSchema.parse({
    mode: "remote",
    baseUrl: "http://127.0.0.1:1933",
    autoCapture: opts?.autoCapture ?? true,
    autoRecall: false,
    commitTokenThreshold: opts?.commitTokenThreshold ?? 20000,
    emitStandardDiagnostics: true,
    ...(opts?.cfgOverrides ?? {}),
  });
  const logger = makeLogger();

  const storedTail = [...(opts?.messageTail ?? [])];
  const addSessionMessage = opts?.addSessionMessageError
    ? vi.fn().mockRejectedValue(opts.addSessionMessageError)
    : vi.fn().mockImplementation(
      async (
        _sessionId: string,
        role: string,
        parts: Array<Record<string, unknown>>,
        _agentId?: string,
        createdAt?: string,
      ) => {
        storedTail.push({
          id: `msg_${storedTail.length + 1}`,
          role,
          parts: JSON.parse(JSON.stringify(parts)),
          created_at: createdAt ?? "2026-05-07T00:00:00.000Z",
        });
      },
    );

  const baseSessionMeta = opts?.getSession ?? { pending_tokens: 100 };
  // The plugin pre-fetches getSession to size the dedup tail (live count
  // only) and re-fetches after writes for the pending-tokens decision.
  // Match that by making message_count track storedTail length unless an
  // explicit override was supplied.
  const overrideMessageCount =
    typeof (baseSessionMeta as { message_count?: number }).message_count === "number";
  const getSession = vi.fn().mockImplementation(async () => ({
    ...baseSessionMeta,
    message_count: overrideMessageCount
      ? (baseSessionMeta as { message_count: number }).message_count
      : storedTail.length,
  }));

  const client = {
    addSessionMessage,
    commitSession: vi.fn().mockResolvedValue({
      status: "accepted",
      task_id: "task-1",
      archived: false,
    }),
    getSession,
    getSessionContext: vi.fn().mockResolvedValue({
      latest_archive_overview: "",
      latest_archive_id: "",
      pre_archive_abstracts: [],
      messages: [],
      estimatedTokens: 0,
      stats: { totalArchives: 0, includedArchives: 0, droppedArchives: 0, failedArchives: 0, activeTokens: 0, archiveTokens: 0 },
    }),
    getSessionMessagesTail: vi.fn().mockImplementation(async (_sid: string, tail: number) => ({
      messages: storedTail.slice(Math.max(0, storedTail.length - tail)),
    })),
  } as unknown as OpenVikingClient;

  const getClient = vi.fn().mockResolvedValue(client);
  const resolveAgentId = vi.fn((_sid: string) => "test-agent");

  const engine = createMemoryOpenVikingContextEngine({
    id: "openviking",
    name: "Test Engine",
    version: "test",
    cfg,
    logger,
    getClient,
    resolveAgentId,
  });

  return {
    engine,
    client: client as unknown as {
      addSessionMessage: ReturnType<typeof vi.fn>;
      commitSession: ReturnType<typeof vi.fn>;
      getSession: ReturnType<typeof vi.fn>;
      getSessionMessagesTail: ReturnType<typeof vi.fn>;
    },
    logger,
    getClient,
  };
}

describe("context-engine afterTurn()", () => {
  it("does nothing when autoCapture is disabled", async () => {
    const { engine, client } = makeEngine({ autoCapture: false });

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages: [{ role: "user", content: "hello" }],
      prePromptMessageCount: 0,
    });

    expect(client.addSessionMessage).not.toHaveBeenCalled();
  });

  it("skips afterTurn completely when the session matches bypassSessionPatterns", async () => {
    const { engine, client, getClient, logger } = makeEngine({
      cfgOverrides: {
        bypassSessionPatterns: ["agent:*:cron:**"],
      },
    });

    await engine.afterTurn!({
      sessionId: "runtime-session",
      sessionKey: "agent:main:cron:nightly:run:1",
      sessionFile: "",
      messages: [{ role: "user", content: "hello" }],
      prePromptMessageCount: 0,
    });

    expect(getClient).not.toHaveBeenCalled();
    expect(client.addSessionMessage).not.toHaveBeenCalled();
    expect(logger.info).toHaveBeenCalledWith(
      expect.stringContaining("\"reason\":\"session_bypassed\""),
    );
  });

  it("skips when messages array is empty", async () => {
    const { engine, client, logger } = makeEngine();

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages: [],
      prePromptMessageCount: 0,
    });

    expect(client.addSessionMessage).not.toHaveBeenCalled();
    expect(logger.info).toHaveBeenCalledWith(
      expect.stringContaining("no_messages"),
    );
  });

  it("skips when no new user/assistant messages after prePromptMessageCount", async () => {
    const { engine, client, logger } = makeEngine();

    const messages = [
      { role: "system", content: "system prompt" },
    ];

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages,
      prePromptMessageCount: 0,
    });

    expect(client.addSessionMessage).not.toHaveBeenCalled();
    expect(logger.info).toHaveBeenCalledWith(
      expect.stringContaining("no_new_turn_messages"),
    );
  });

  it("stores new messages via addSessionMessage with proper roles", async () => {
    const { engine, client } = makeEngine();

    const messages = [
      { role: "user", content: "old message" },
      { role: "user", content: "hello world, this is a new message" },
      { role: "assistant", content: [{ type: "text", text: "hi there, nice to meet you" }] },
    ];

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages,
      prePromptMessageCount: 1,
    });

    expect(client.addSessionMessage).toHaveBeenCalledTimes(2);
    // First call: user message
    expect(client.addSessionMessage.mock.calls[0][1]).toBe("user");
    expect(client.addSessionMessage.mock.calls[0][2][0].text).toContain("hello world");
    // Second call: assistant message
    expect(client.addSessionMessage.mock.calls[1][1]).toBe("assistant");
    expect(client.addSessionMessage.mock.calls[1][2][0].text).toContain("hi there");
  });

  it("stores a real new single-message turn even when it repeats persisted user content (review regression)", async () => {
    // The published reviewer-reproduced regression for the previous
    // raw-tail dedup attempt: user types the same content across two
    // turns. Stored tail at the start of turn 2 is [user, assistant]; the
    // incoming batch carries only the new user turn (single message).
    // The deduplicateAfterTurnBatch routine falls through to the
    // oversized branch (stored > incoming), tail-match fails (stored
    // last is the assistant reply), suffix-fallback finds no overlap,
    // and the OV-tuned default policy is "ingest" rather than "skip" —
    // so the second user message is preserved.
    const { engine, client } = makeEngine({
      messageTail: [
        {
          id: "msg_existing_user",
          role: "user",
          parts: [{ type: "text", text: "same answer" }],
          created_at: "2026-05-07T00:00:00.000Z",
        },
        {
          id: "msg_existing_assistant",
          role: "assistant",
          parts: [{ type: "text", text: "first reply" }],
          created_at: "2026-05-07T00:00:01.000Z",
        },
      ],
    });

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages: [{ role: "user", content: "same answer" }],
      prePromptMessageCount: 0,
    });

    expect(client.getSessionMessagesTail).toHaveBeenCalled();
    expect(client.addSessionMessage).toHaveBeenCalledTimes(1);
    expect(client.addSessionMessage.mock.calls[0][1]).toBe("user");
    expect(client.addSessionMessage.mock.calls[0][2][0].text).toBe("same answer");
  });

  it("stores a user-only repeated message when persisted tail contains only the same user", async () => {
    const { engine, client } = makeEngine({
      messageTail: [
        {
          id: "msg_existing_user",
          role: "user",
          parts: [{ type: "text", text: "same answer" }],
          created_at: "2026-05-07T00:00:00.000Z",
        },
      ],
    });

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages: [{ role: "user", content: "same answer" }],
      prePromptMessageCount: 0,
    });

    expect(client.getSessionMessagesTail).toHaveBeenCalled();
    expect(client.addSessionMessage).toHaveBeenCalledTimes(1);
    expect(client.addSessionMessage.mock.calls[0][1]).toBe("user");
    expect(client.addSessionMessage.mock.calls[0][2][0].text).toBe("same answer");
  });

  it("treats empty tool_input and missing tool_input as the same signature", () => {
    const withEmptyInput = capturedMessageSignature({
      role: "user",
      parts: [{
        type: "tool",
        tool_id: "call_1",
        tool_name: "diagnostic_tool",
        tool_input: {},
        tool_output: "ok",
        tool_status: "success",
      }],
    });
    const withMissingInput = capturedMessageSignature({
      role: "user",
      parts: [{
        type: "tool",
        tool_id: "call_1",
        tool_name: "diagnostic_tool",
        tool_output: "ok",
        tool_status: "success",
      }],
    });

    expect(withEmptyInput).toBe(withMissingInput);
  });

  it("keeps a repeated leading user when only one stored message exists", async () => {
    const { engine, client } = makeEngine();

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages: [{ role: "user", content: "hello from loop hook" }],
      prePromptMessageCount: 0,
    });

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages: [
        { role: "user", content: "hello from loop hook" },
        { role: "assistant", content: "final answer" },
      ],
      prePromptMessageCount: 0,
    });

    expect(client.addSessionMessage).toHaveBeenCalledTimes(3);
    expect(client.addSessionMessage.mock.calls[0][1]).toBe("user");
    expect(client.addSessionMessage.mock.calls[0][2][0].text).toContain("hello from loop hook");
    expect(client.addSessionMessage.mock.calls[1][1]).toBe("user");
    expect(client.addSessionMessage.mock.calls[1][2][0].text).toContain("hello from loop hook");
    expect(client.addSessionMessage.mock.calls[2][1]).toBe("assistant");
    expect(client.addSessionMessage.mock.calls[2][2][0].text).toContain("final answer");
  });

  it("keeps a fully repeated user and assistant turn instead of treating content equality as replay", async () => {
    const { engine, client } = makeEngine();

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages: [
        { role: "user", content: "ping" },
        { role: "assistant", content: "pong" },
      ],
      prePromptMessageCount: 0,
    });

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages: [
        { role: "user", content: "ping" },
        { role: "assistant", content: "pong" },
      ],
      prePromptMessageCount: 0,
    });

    expect(client.addSessionMessage).toHaveBeenCalledTimes(4);
    expect(client.addSessionMessage.mock.calls.map((call) => call[1])).toEqual([
      "user",
      "assistant",
      "user",
      "assistant",
    ]);
  });

  it("does not use a single persisted raw transcript tail message as replay proof after a plugin restart", async () => {
    const { engine, client, logger } = makeEngine({
      messageTail: [
        {
          id: "msg_existing_user",
          role: "user",
          parts: [{ type: "text", text: "hello from stored tail" }],
          created_at: "2026-05-07T00:00:00.000Z",
        },
      ],
    });

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages: [
        { role: "user", content: "hello from stored tail" },
        { role: "assistant", content: "final answer after restart" },
      ],
      prePromptMessageCount: 0,
    });

    // tailLimit equals the live message_count returned by getSession,
    // never a commitKeepRecentCount-derived value: the dedup compares
    // against the OV live tail only and must avoid crossing into
    // archives, where the runtime's incoming batch (built from a
    // post-archive coordinate space) cannot align.
    expect(client.getSessionMessagesTail).toHaveBeenCalledWith("s1", 1, "test-agent");
    expect(client.addSessionMessage).toHaveBeenCalledTimes(2);
    expect(client.addSessionMessage.mock.calls[0][1]).toBe("user");
    expect(client.addSessionMessage.mock.calls[0][2][0].text).toContain("hello from stored tail");
    expect(client.addSessionMessage.mock.calls[1][1]).toBe("assistant");
    expect(client.addSessionMessage.mock.calls[1][2][0].text).toContain("final answer after restart");
    expect(logger.info).not.toHaveBeenCalledWith(
      expect.stringContaining('"stage":"afterTurn_tail_dedup"'),
    );
  });

  it("sizes the persisted-tail fetch by live message_count, not by config", async () => {
    const { engine, client } = makeEngine({
      cfgOverrides: { commitKeepRecentCount: 24 },
      messageTail: Array.from({ length: 5 }, (_, idx) => ({
        id: `msg_${idx + 1}`,
        role: idx % 2 === 0 ? "user" : "assistant",
        parts: [{ type: "text", text: `pre-existing ${idx + 1}` }],
        created_at: "2026-05-07T00:00:00.000Z",
      })),
    });

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages: [
        { role: "user", content: "live-count drives tailLimit" },
        { role: "assistant", content: "regardless of commitKeepRecentCount" },
      ],
      prePromptMessageCount: 0,
    });

    // 5 stored messages = live message_count = exact tailLimit
    expect(client.getSessionMessagesTail).toHaveBeenCalledWith("s1", 5, "test-agent");
  });

  it("skips the persisted-tail fetch entirely when the live session is empty", async () => {
    const { engine, client } = makeEngine();

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages: Array.from({ length: 8 }, (_, index) => ({
        role: index % 2 === 0 ? "user" : "assistant",
        content: `first turn message ${index + 1}`,
      })),
      prePromptMessageCount: 0,
    });

    // Empty live session => no remote tail to compare against; we
    // shouldn't waste a round trip on dedup.
    expect(client.getSessionMessagesTail).not.toHaveBeenCalled();
    expect(client.addSessionMessage).toHaveBeenCalledTimes(8);
  });

  it("skips replayed tool-loop transcript messages already captured for the session", async () => {
    const { engine, client, logger } = makeEngine();
    const userMessage = { role: "user", content: "store this locomo conversation" };
    const toolCall = {
      role: "assistant",
      content: [
        { type: "text", text: "I will store it first." },
        { type: "toolUse", id: "call_1", name: "memory_store", input: { text: "locomo facts" } },
      ],
    };
    const toolResult = {
      role: "toolResult",
      toolCallId: "call_1",
      toolName: "memory_store",
      content: "Stored in OpenViking and committed 6 memories.",
    };

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages: [userMessage, toolCall, toolResult],
      prePromptMessageCount: 0,
    });

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages: [
        userMessage,
        toolCall,
        toolResult,
        { role: "assistant", content: "Stored. Here is the recap." },
      ],
      prePromptMessageCount: 0,
    });

    expect(client.addSessionMessage).toHaveBeenCalledTimes(4);
    expect(client.addSessionMessage.mock.calls.map((call) => call[1])).toEqual([
      "user",
      "assistant",
      "user",
      "assistant",
    ]);
    expect(client.addSessionMessage.mock.calls[3][2][0].text).toContain("Here is the recap");
    expect(logger.info).toHaveBeenCalledWith(
      expect.stringContaining('"stage":"afterTurn_tail_dedup"'),
    );
    expect(logger.info).toHaveBeenCalledWith(
      expect.stringContaining('"skippedMessages":3'),
    );
  });

  it("keeps an entire identical finalizer transcript because content-only proof is ambiguous", async () => {
    const { engine, client, logger } = makeEngine();
    const messages = [
      { role: "user", content: "please run the diagnostic tool once" },
      {
        role: "assistant",
        content: [
          { type: "text", text: "I will run it now." },
          { type: "toolCall", id: "call_1", name: "diagnostic_tool", arguments: { scope: "afterTurn" } },
        ],
      },
      {
        role: "toolResult",
        toolCallId: "call_1",
        toolName: "diagnostic_tool",
        content: [{ type: "text", text: "diagnostic result: ok" }],
      },
      { role: "assistant", content: "The diagnostic finished cleanly." },
    ];

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages,
      prePromptMessageCount: 0,
    });

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages,
      prePromptMessageCount: 0,
    });

    expect(client.addSessionMessage).toHaveBeenCalledTimes(8);
    expect(client.addSessionMessage.mock.calls.map((call) => call[1])).toEqual([
      "user",
      "assistant",
      "user",
      "assistant",
      "user",
      "assistant",
      "user",
      "assistant",
    ]);
    expect(logger.info).not.toHaveBeenCalledWith(
      expect.stringContaining('"stage":"afterTurn_tail_dedup"'),
    );
  });

  it("passes the latest non-system message timestamp to addSessionMessage as ISO string", async () => {
    const { engine, client } = makeEngine();

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages: [
        { role: "user", content: "old message", timestamp: 1775037600000 },
        { role: "user", content: "new message", timestamp: 1775037660000 },
        { role: "assistant", content: "new reply", timestamp: 1775037720000 },
        { role: "toolResult", toolName: "bash", content: "exit 0", timestamp: 1775037780000 },
        { role: "system", content: "ignored system message", timestamp: 1775037840000 },
      ],
      prePromptMessageCount: 1,
    });

    // user + assistant + toolResult(→user) = 3 calls (toolResult merges with no adjacent user)
    expect(client.addSessionMessage).toHaveBeenCalled();
    const lastCallIdx = client.addSessionMessage.mock.calls.length - 1;
    const createdAt = client.addSessionMessage.mock.calls[lastCallIdx][4] as string;
    expect(createdAt).toBe("2026-04-01T10:03:00.000Z");
  });

  it("records senderId from runtimeContext in afterTurn diagnostics", async () => {
    const { engine, logger } = makeEngine({
      commitTokenThreshold: 50,
      getSession: { pending_tokens: 5000 },
    });

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages: [{ role: "user", content: "hello world" }],
      prePromptMessageCount: 0,
      runtimeContext: { senderId: "telegram:12345" },
    });

    expect(logger.info).toHaveBeenCalledWith(
      expect.stringContaining("\"senderIdFound\":true"),
    );
    expect(logger.info).toHaveBeenCalledWith(
      expect.stringContaining("\"senderId\":\"telegram:12345\""),
    );
  });

  it("passes sanitized senderId as role_id", async () => {
    const { engine, client } = makeEngine();

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages: [{ role: "user", content: "hello world" }],
      prePromptMessageCount: 0,
      runtimeContext: { senderId: "telegram:12345" },
    });

    expect(client.addSessionMessage).toHaveBeenCalledTimes(1);
    expect(client.addSessionMessage.mock.calls[0][5]).toBe("telegram_12345");
  });

  it("sanitizes <relevant-memories> from user content but not from assistant", async () => {
    const { engine, client } = makeEngine();

    const messages = [
      {
        role: "user",
        content: "my question <relevant-memories>injected memory data</relevant-memories> more text",
      },
    ];

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages,
      prePromptMessageCount: 0,
    });

    expect(client.addSessionMessage).toHaveBeenCalledTimes(1);
    expect(client.addSessionMessage.mock.calls[0][1]).toBe("user");
    const storedContent = (client.addSessionMessage.mock.calls[0][2] as Array<{ text?: string }>)[0].text;
    expect(storedContent).not.toContain("relevant-memories");
    expect(storedContent).not.toContain("injected memory data");
    expect(storedContent).toContain("my question");
  });

  it("does not commit when pendingTokens < threshold", async () => {
    const { engine, client } = makeEngine({
      commitTokenThreshold: 20000,
      getSession: { pending_tokens: 100 },
    });

    const messages = [
      { role: "user", content: "some meaningful content here for testing" },
    ];

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages,
      prePromptMessageCount: 0,
    });

    expect(client.addSessionMessage).toHaveBeenCalledTimes(1);
    expect(client.commitSession).not.toHaveBeenCalled();
  });

  it("commits when pendingTokens >= threshold", async () => {
    const { engine, client } = makeEngine({
      commitTokenThreshold: 20000,
      getSession: { pending_tokens: 25000 },
    });

    const messages = [
      { role: "user", content: "some meaningful content here for testing" },
    ];

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages,
      prePromptMessageCount: 0,
    });

    expect(client.addSessionMessage).toHaveBeenCalledTimes(1);
    expect(client.commitSession).toHaveBeenCalledTimes(1);
    const commitCall = client.commitSession.mock.calls[0];
    expect(commitCall[1]).toMatchObject({ wait: false });
  });

  it("catches errors without throwing", async () => {
    const { engine, logger } = makeEngine({
      addSessionMessageError: new Error("network timeout"),
    });

    const messages = [
      { role: "user", content: "this will fail when storing to OV" },
    ];

    await expect(
      engine.afterTurn!({
        sessionId: "s1",
        sessionFile: "",
        messages,
        prePromptMessageCount: 0,
      }),
    ).resolves.toBeUndefined();

    expect(logger.warn).toHaveBeenCalledWith(
      expect.stringContaining("afterTurn failed"),
    );
  });

  it("commit uses OV session ID derived from sessionId", async () => {
    const { engine, client } = makeEngine({
      commitTokenThreshold: 100,
      getSession: { pending_tokens: 5000 },
    });

    const messages = [
      { role: "user", content: "enough content to trigger commit logic path" },
    ];

    await engine.afterTurn!({
      sessionId: "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
      sessionFile: "",
      messages,
      prePromptMessageCount: 0,
    });

    expect(client.commitSession).toHaveBeenCalledTimes(1);
    const commitSessionId = client.commitSession.mock.calls[0][0] as string;
    expect(commitSessionId).toBe("a1b2c3d4-e5f6-7890-abcd-ef1234567890");
  });

  it("commit passes wait=false for afterTurn (async Phase 2)", async () => {
    const { engine, client } = makeEngine({
      commitTokenThreshold: 100,
      getSession: { pending_tokens: 5000 },
    });

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages: [{ role: "user", content: "triggering commit with enough tokens" }],
      prePromptMessageCount: 0,
    });

    expect(client.commitSession).toHaveBeenCalledTimes(1);
    expect(client.commitSession.mock.calls[0][1]).toMatchObject({ wait: false });
  });

  it("calls addSessionMessage with OV session ID as first arg", async () => {
    const { engine, client } = makeEngine();

    await engine.afterTurn!({
      sessionId: "my-session",
      sessionFile: "",
      messages: [{ role: "user", content: "content for session storage" }],
      prePromptMessageCount: 0,
    });

    expect(client.addSessionMessage).toHaveBeenCalledTimes(1);
    const ovSessionId = client.addSessionMessage.mock.calls[0][0] as string;
    expect(ovSessionId).toBe("my-session");
  });

  it("preserves code snippets and file paths in captured content", async () => {
    const { engine, client } = makeEngine();

    const messages = [
      {
        role: "user",
        content: "Look at src/app.ts and run `npm install`",
      },
      {
        role: "assistant",
        content: [{ type: "text", text: "Here's the code:\n```typescript\nexport const x = 1;\n```" }],
      },
    ];

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages,
      prePromptMessageCount: 0,
    });

    expect(client.addSessionMessage).toHaveBeenCalledTimes(2);
    const userParts = client.addSessionMessage.mock.calls[0][2] as Array<{ text?: string }>;
    const assistantParts = client.addSessionMessage.mock.calls[1][2] as Array<{ text?: string }>;
    expect(userParts.map(p => p.text).join(" ")).toContain("src/app.ts");
    expect(userParts.map(p => p.text).join(" ")).toContain("npm install");
    expect(assistantParts.map(p => p.text).join(" ")).toContain("export const x = 1");
  });

  it("passes agentId to addSessionMessage", async () => {
    const { engine, client } = makeEngine();

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages: [{ role: "user", content: "test message for agent routing" }],
      prePromptMessageCount: 0,
    });

    expect(client.addSessionMessage).toHaveBeenCalledTimes(1);
    const agentId = client.addSessionMessage.mock.calls[0][3] as string;
    expect(agentId).toBe("test-agent");
  });

  it("checks pending tokens after addSessionMessage", async () => {
    const { engine, client } = makeEngine({
      getSession: { pending_tokens: 500 },
    });

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages: [{ role: "user", content: "check pending token flow" }],
      prePromptMessageCount: 0,
    });

    expect(client.addSessionMessage).toHaveBeenCalled();
    expect(client.getSession).toHaveBeenCalled();
  });

  it("maps toolResult to user role", async () => {
    const { engine, client } = makeEngine();

    const messages = [
      { role: "assistant", content: [
        { type: "text", text: "running tool" },
        { type: "toolUse", name: "bash", input: { cmd: "ls" } },
      ] },
      { role: "toolResult", toolName: "bash", content: "file1.txt\nfile2.txt" },
      { role: "assistant", content: "done" },
    ];

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages,
      prePromptMessageCount: 0,
    });

    expect(client.addSessionMessage).toHaveBeenCalledTimes(3);
    // assistant → user(toolResult) → assistant
    expect(client.addSessionMessage.mock.calls[0][1]).toBe("assistant");
    expect(client.addSessionMessage.mock.calls[1][1]).toBe("user");
    expect(client.addSessionMessage.mock.calls[1][2][0].tool_output).toContain("file1.txt");
    expect(client.addSessionMessage.mock.calls[1][2][0].tool_output).toContain("file2.txt");
    expect(client.addSessionMessage.mock.calls[2][1]).toBe("assistant");
  });

  it("stores adjacent same-role messages as separate entries with current extractor behavior", async () => {
    const { engine, client } = makeEngine();

    const messages = [
      { role: "user", content: "first question" },
      { role: "user", content: "second question" },
      { role: "assistant", content: "answer" },
    ];

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages,
      prePromptMessageCount: 0,
    });

    expect(client.addSessionMessage).toHaveBeenCalledTimes(3);
    expect(client.addSessionMessage.mock.calls[0][1]).toBe("user");
    const firstCallParts = client.addSessionMessage.mock.calls[0][2] as Array<{ text?: string; type?: string }>;
    expect(firstCallParts.map(p => p.text).join(" ")).toContain("first question");
    expect(client.addSessionMessage.mock.calls[1][1]).toBe("user");
    const secondCallParts = client.addSessionMessage.mock.calls[1][2] as Array<{ text?: string; type?: string }>;
    expect(secondCallParts.map(p => p.text).join(" ")).toContain("second question");
    expect(client.addSessionMessage.mock.calls[2][1]).toBe("assistant");
  });

  it("stores adjacent toolResults as separate user groups with current extractor behavior", async () => {
    const { engine, client } = makeEngine();

    const messages = [
      { role: "assistant", content: [
        { type: "text", text: "calling tools" },
        { type: "toolUse", name: "read", input: { path: "a.txt" } },
      ] },
      { role: "toolResult", toolName: "read", content: "content of a" },
      { role: "toolResult", toolName: "write", content: "ok" },
      { role: "assistant", content: "all done" },
    ];

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages,
      prePromptMessageCount: 0,
    });

    expect(client.addSessionMessage).toHaveBeenCalledTimes(4);
    expect(client.addSessionMessage.mock.calls[0][1]).toBe("assistant");
    expect(client.addSessionMessage.mock.calls[1][1]).toBe("user");
    expect((client.addSessionMessage.mock.calls[1][2] as Array<{ tool_output?: string }>)[0]?.tool_output).toContain("content of a");
    expect(client.addSessionMessage.mock.calls[2][1]).toBe("user");
    expect((client.addSessionMessage.mock.calls[2][2] as Array<{ tool_output?: string }>)[0]?.tool_output).toContain("ok");
    expect(client.addSessionMessage.mock.calls[3][1]).toBe("assistant");
  });

  it("sanitizes <relevant-memories> from assistant content", async () => {
    const { engine, client } = makeEngine();

    const messages = [
      { role: "user", content: "question" },
      { role: "assistant", content: "Here is context <relevant-memories>data</relevant-memories> end" },
    ];

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages,
      prePromptMessageCount: 0,
    });

    expect(client.addSessionMessage).toHaveBeenCalledTimes(2);
    const assistantParts = client.addSessionMessage.mock.calls[1][2] as Array<{ text?: string }>;
    expect(assistantParts.map(p => p.text).join(" ")).not.toContain("relevant-memories");
    expect(assistantParts.map(p => p.text).join(" ")).toContain("Here is context");
  });

  it("skips heartbeat messages from being stored", async () => {
    const { engine, client } = makeEngine();

    const messages = [
      { role: "user", content: "Read HEARTBEAT.md if it exists (workspace context). Follow it strictly. Do not infer or repeat old tasks from prior chats. If nothing needs attention, reply HEARTBEAT_OK." },
      { role: "assistant", content: "HEARTBEAT_OK" },
    ];

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages,
      prePromptMessageCount: 0,
    });

    expect(client.addSessionMessage).not.toHaveBeenCalled();
  });

  it("skips heartbeat via isHeartbeat flag", async () => {
    const { engine, client } = makeEngine();

    const messages = [
      { role: "user", content: "regular message" },
      { role: "assistant", content: "reply" },
    ];

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages,
      prePromptMessageCount: 0,
      isHeartbeat: true,
    });

    expect(client.addSessionMessage).not.toHaveBeenCalled();
  });

  it("skips store when all new messages are system only", async () => {
    const { engine, client } = makeEngine();

    // Only system messages after prePromptMessageCount → no user/assistant texts extracted
    const messages = [
      { role: "user", content: "previous message" },
      { role: "system", content: "system prompt injection" },
    ];

    await engine.afterTurn!({
      sessionId: "s1",
      sessionFile: "",
      messages,
      prePromptMessageCount: 1,
    });

    expect(client.addSessionMessage).not.toHaveBeenCalled();
  });
});
