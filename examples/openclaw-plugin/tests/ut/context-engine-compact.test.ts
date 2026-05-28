import { describe, expect, it, vi } from "vitest";

import type { OpenVikingClient } from "../../client.js";
import { memoryOpenVikingConfigSchema } from "../../config.js";
import {
  createMemoryOpenVikingContextEngine,
  openClawSessionToOvStorageId,
} from "../../context-engine.js";

type TestSessionContext = Awaited<ReturnType<OpenVikingClient["getSessionContext"]>>;

function makeLogger() {
  return {
    info: vi.fn(),
    warn: vi.fn(),
    error: vi.fn(),
  };
}

function makeEngine(commitResult: unknown, opts?: {
  throwError?: Error;
  sessionContext?: TestSessionContext;
  configOverrides?: Record<string, unknown>;
}) {
  const cfg = memoryOpenVikingConfigSchema.parse({
    mode: "remote",
    baseUrl: "http://127.0.0.1:1933",
    autoCapture: false,
    autoRecall: false,
    ...(opts?.configOverrides ?? {}),
  });
  const logger = makeLogger();

  const commitSession = opts?.throwError
    ? vi.fn().mockRejectedValue(opts.throwError)
    : vi.fn().mockResolvedValue(commitResult);

  const client = {
    commitSession,
    getSessionContext: vi.fn().mockResolvedValue(opts?.sessionContext ?? {
      latest_archive_overview: "",
      latest_archive_id: "",
      pre_archive_abstracts: [],
      messages: [],
      estimatedTokens: 0,
      stats: { totalArchives: 0, includedArchives: 0, droppedArchives: 0, failedArchives: 0, activeTokens: 0, archiveTokens: 0 },
    }),
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
      commitSession: ReturnType<typeof vi.fn>;
      getSessionContext: ReturnType<typeof vi.fn>;
    },
    logger,
    resolveAgentId,
  };
}

function interceptRequest(overrides?: Record<string, unknown>) {
  return {
    sessionId: "s-intercept",
    sessionFile: "session.jsonl",
    tokenBudget: 4096,
    currentTokenCount: 3900,
    firstKeptEntryId: "pi-entry-42",
    tokensBefore: 3900,
    ...(overrides ?? {}),
  };
}

describe("context-engine commitOVSession()", () => {
  it("returns true on successful commit", async () => {
    const { engine } = makeEngine({
      status: "completed",
      archived: false,
      memories_extracted: { core: 1 },
    });

    const ok = await engine.commitOVSession({ sessionId: "test-session" });
    expect(ok).toBe(true);
  });

  it("returns false on failed commit", async () => {
    const { engine } = makeEngine({
      status: "failed",
      error: "extraction error",
    });

    const ok = await engine.commitOVSession({ sessionId: "test-session" });
    expect(ok).toBe(false);
  });

  it("returns false on timeout commit", async () => {
    const { engine } = makeEngine({
      status: "timeout",
      task_id: "task-timeout",
    });

    const ok = await engine.commitOVSession({ sessionId: "test-session" });
    expect(ok).toBe(false);
  });

  it("returns false when commit throws", async () => {
    const { engine } = makeEngine(null, {
      throwError: new Error("connection refused"),
    });

    const ok = await engine.commitOVSession({ sessionId: "test-session" });
    expect(ok).toBe(false);
  });

  it("uses wait=true for synchronous extraction", async () => {
    const { engine, client } = makeEngine({
      status: "completed",
      archived: false,
      memories_extracted: {},
    });

    await engine.commitOVSession({ sessionId: "s1" });

    expect(client.commitSession.mock.calls[0][1]).toMatchObject({ wait: true });
  });

  it("uses sessionKey-derived OV session ID for commitOVSession", async () => {
    const { engine, client, resolveAgentId } = makeEngine({
      status: "completed",
      archived: false,
      memories_extracted: {},
    });

    await engine.commitOVSession({
      sessionId: "plain-session",
      sessionKey: "agent:main:main",
    });

    const ovSessionId = openClawSessionToOvStorageId("plain-session", "agent:main:main");
    expect(client.commitSession.mock.calls[0][0]).toBe(ovSessionId);
    expect(resolveAgentId).toHaveBeenCalledWith("plain-session", "agent:main:main", ovSessionId);
  });

  it("logs memories extracted count", async () => {
    const { engine, logger } = makeEngine({
      status: "completed",
      archived: true,
      memories_extracted: { core: 3, preferences: 1 },
    });

    await engine.commitOVSession({ sessionId: "s1" });

    expect(logger.info).toHaveBeenCalledWith(
      expect.stringContaining("memories=4"),
    );
  });

  it("skips commitOVSession when the session matches bypassSessionPatterns", async () => {
    const cfg = memoryOpenVikingConfigSchema.parse({
      mode: "remote",
      baseUrl: "http://127.0.0.1:1933",
      autoCapture: false,
      autoRecall: false,
      bypassSessionPatterns: ["agent:*:cron:**"],
    });
    const logger = makeLogger();
    const getClient = vi.fn();
    const resolveAgentId = vi.fn((_sid: string) => "test-agent");

    const engine = createMemoryOpenVikingContextEngine({
      id: "openviking",
      name: "Test Engine",
      version: "test",
      cfg,
      logger,
      getClient: getClient as any,
      resolveAgentId,
    });

    const ok = await engine.commitOVSession({ sessionId: "runtime-session", sessionKey: "agent:main:cron:nightly:run:1" });

    expect(ok).toBe(false);
    expect(getClient).not.toHaveBeenCalled();
    expect(logger.warn).toHaveBeenCalledWith(
      expect.stringContaining("session is bypassed"),
    );
  });
});

describe("context-engine compact()", () => {
  it("returns compacted=false when the session matches bypassSessionPatterns", async () => {
    const cfg = memoryOpenVikingConfigSchema.parse({
      mode: "remote",
      baseUrl: "http://127.0.0.1:1933",
      autoCapture: false,
      autoRecall: false,
      bypassSessionPatterns: ["agent:*:cron:**"],
    });
    const logger = makeLogger();
    const getClient = vi.fn();
    const resolveAgentId = vi.fn((_sid: string) => "test-agent");

    const engine = createMemoryOpenVikingContextEngine({
      id: "openviking",
      name: "Test Engine",
      version: "test",
      cfg,
      logger,
      getClient: getClient as any,
      resolveAgentId,
    });

    const result = await engine.compact({
      sessionId: "agent:main:cron:nightly:run:1",
      sessionFile: "",
    });

    expect(result).toEqual({
      ok: true,
      compacted: false,
      reason: "session_bypassed",
    });
    expect(getClient).not.toHaveBeenCalled();
  });

  it("returns compacted=true when commit succeeds with archived=true", async () => {
    const { engine } = makeEngine({
      status: "completed",
      archived: true,
      task_id: "task-1",
      memories_extracted: { core: 3, preferences: 1 },
    });

    const result = await engine.compact({
      sessionId: "s1",
      sessionFile: "",
    });

    expect(result.ok).toBe(true);
    expect(result.compacted).toBe(true);
    expect(result.reason).toBe("commit_completed");
  });

  it("returns compacted=false when commit succeeds with archived=false", async () => {
    const { engine } = makeEngine({
      status: "completed",
      archived: false,
      task_id: "task-2",
      memories_extracted: {},
    });

    const result = await engine.compact({
      sessionId: "s2",
      sessionFile: "",
    });

    expect(result.ok).toBe(true);
    expect(result.compacted).toBe(false);
    expect(result.reason).toBe("commit_no_archive");
  });

  it("returns ok=false when commit status is 'failed'", async () => {
    const { engine, logger } = makeEngine({
      status: "failed",
      error: "extraction pipeline error",
      task_id: "task-3",
    });

    const result = await engine.compact({
      sessionId: "s3",
      sessionFile: "",
    });

    expect(result.ok).toBe(false);
    expect(result.compacted).toBe(false);
    expect(result.reason).toBe("commit_failed");
    expect(logger.warn).toHaveBeenCalledWith(
      expect.stringContaining("Phase 2 failed"),
    );
  });

  it("returns ok=false when commit status is 'timeout'", async () => {
    const { engine, logger } = makeEngine({
      status: "timeout",
      task_id: "task-4",
    });

    const result = await engine.compact({
      sessionId: "s4",
      sessionFile: "",
    });

    expect(result.ok).toBe(false);
    expect(result.compacted).toBe(false);
    expect(result.reason).toBe("commit_timeout");
    expect(logger.warn).toHaveBeenCalledWith(
      expect.stringContaining("Phase 2 timed out"),
    );
  });

  it("commit passes wait=true for synchronous extraction", async () => {
    const { engine, client } = makeEngine({
      status: "completed",
      archived: true,
      memories_extracted: { core: 2 },
    });

    await engine.compact({ sessionId: "s1", sessionFile: "" });

    expect(client.commitSession).toHaveBeenCalledTimes(1);
    expect(client.commitSession.mock.calls[0][1]).toMatchObject({ wait: true });
  });

  it("logs memory extraction count on success", async () => {
    const { engine, logger } = makeEngine({
      status: "completed",
      archived: true,
      task_id: "task-mem",
      memories_extracted: { core: 5, preferences: 2 },
    });

    await engine.compact({ sessionId: "s1", sessionFile: "" });

    expect(logger.info).toHaveBeenCalledWith(
      expect.stringContaining("memories=7"),
    );
  });

  it("handles commit with zero memories extracted", async () => {
    const { engine, logger } = makeEngine({
      status: "completed",
      archived: true,
      task_id: "task-empty",
      memories_extracted: {},
    });

    await engine.compact({ sessionId: "s-empty", sessionFile: "" });

    expect(logger.info).toHaveBeenCalledWith(
      expect.stringContaining("memories=0"),
    );
  });

  it("handles commit with missing memories_extracted field", async () => {
    const { engine } = makeEngine({
      status: "completed",
      archived: false,
    });

    const result = await engine.compact({ sessionId: "s-no-mem", sessionFile: "" });
    expect(result.ok).toBe(true);
  });

  it("uses correct OV session ID derived from sessionId", async () => {
    const { engine, client } = makeEngine({
      status: "completed",
      archived: false,
      memories_extracted: {},
    });

    await engine.compact({
      sessionId: "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
      sessionFile: "",
    });

    const commitCallSessionId = client.commitSession.mock.calls[0][0] as string;
    expect(commitCallSessionId).toBe("a1b2c3d4-e5f6-7890-abcd-ef1234567890");
  });

  it("uses sessionKey-derived OV session ID and resolver context when compact receives a non-UUID sessionId", async () => {
    const { engine, client, resolveAgentId } = makeEngine({
      status: "completed",
      archived: false,
      memories_extracted: {},
    });

    await engine.compact({
      sessionId: "plain-session",
      sessionFile: "",
      runtimeContext: { sessionKey: "agent:main:main", agentId: "main" },
    });

    const ovSessionId = openClawSessionToOvStorageId("plain-session", "agent:main:main");
    expect(client.commitSession.mock.calls[0][0]).toBe(ovSessionId);
    expect(resolveAgentId).toHaveBeenCalledWith("plain-session", "agent:main:main", ovSessionId);
  });

  it("prefers top-level sessionKey over runtimeContext sessionKey in compact params", async () => {
    const { engine, client, resolveAgentId } = makeEngine({
      status: "completed",
      archived: false,
      memories_extracted: {},
    });

    await engine.compact({
      sessionId: "plain-session",
      sessionKey: "agent:top:main",
      sessionFile: "",
      runtimeContext: { sessionKey: "agent:runtime:main" },
    });

    const ovSessionId = openClawSessionToOvStorageId("plain-session", "agent:top:main");
    expect(client.commitSession.mock.calls[0][0]).toBe(ovSessionId);
    expect(resolveAgentId).toHaveBeenCalledWith("plain-session", "agent:top:main", ovSessionId);
  });

  it("passes agentId to commitSession", async () => {
    const { engine, client } = makeEngine({
      status: "completed",
      archived: false,
      memories_extracted: {},
    });

    await engine.compact({ sessionId: "s1", sessionFile: "" });

    expect(client.commitSession.mock.calls[0][1]).toMatchObject({
      agentId: "test-agent",
    });
  });

  it("returns ok=false with reason=commit_error when commit throws", async () => {
    const { engine, logger } = makeEngine(null, {
      throwError: new Error("network unreachable"),
    });

    const result = await engine.compact({
      sessionId: "s5",
      sessionFile: "",
    });

    expect(result.ok).toBe(false);
    expect(result.compacted).toBe(false);
    expect(result.reason).toBe("commit_error");
    expect(logger.warn).toHaveBeenCalledWith(
      expect.stringContaining("commit failed"),
    );
  });

  it("returns compacted=false when commit reports the OV session does not exist", async () => {
    const { engine, client, logger } = makeEngine(null, {
      throwError: new Error("OpenViking request failed [NOT_FOUND]: Session not found: s-missing"),
    });

    const result = await engine.compact({
      sessionId: "s-missing",
      sessionFile: "",
      currentTokenCount: 123,
    });

    expect(result.ok).toBe(true);
    expect(result.compacted).toBe(false);
    expect(result.reason).toBe("session_not_found");
    expect(client.commitSession).toHaveBeenCalledTimes(1);
    expect(logger.warn).not.toHaveBeenCalledWith(
      expect.stringContaining("compact commit failed"),
    );
  });
});

describe("context-engine interceptCompaction()", () => {
  it("advertises compaction interception and keeps recent messages when handling Pi compaction", async () => {
    const { engine, client } = makeEngine({
      status: "completed",
      archived: true,
      archive_uri: "ov://archive/archive-1",
      task_id: "task-intercept",
      memories_extracted: { core: 1 },
    }, {
      sessionContext: {
        latest_archive_overview: "The user is validating compaction intercept.",
        latest_archive_id: "archive-1",
        pre_archive_abstracts: [],
        messages: [{
          id: "live-1",
          role: "user",
          parts: [{ type: "text", text: "Please keep this recent tail live." }],
          created_at: "2026-05-28T10:00:00.000Z",
        }],
        estimatedTokens: 321,
        stats: { totalArchives: 1, includedArchives: 1, droppedArchives: 0, failedArchives: 0, activeTokens: 120, archiveTokens: 201 },
      },
    });

    expect((engine.info as any).interceptsCompaction).toBe(true);

    const result = await (engine as any).interceptCompaction({
      sessionId: "s-intercept",
      sessionFile: "session.jsonl",
      tokenBudget: 4096,
      currentTokenCount: 3900,
      firstKeptEntryId: "pi-entry-42",
      tokensBefore: 3900,
    });

    expect(client.commitSession).toHaveBeenCalledTimes(1);
    expect(client.commitSession.mock.calls[0][1]).toMatchObject({
      wait: true,
      keepRecentCount: 10,
    });
    expect(result).toMatchObject({
      handled: true,
      firstKeptEntryId: "pi-entry-42",
      tokensBefore: 3900,
    });
    expect(result.tokensAfter).toBeGreaterThan(0);
    expect(result.summary).toContain("The user is validating compaction intercept.");
    expect(result.summary).toContain("Please keep this recent tail live.");
  });

  it("declines before touching OV when the compaction signal is already aborted", async () => {
    const { engine, client } = makeEngine({
      status: "completed",
      archived: true,
      memories_extracted: {},
    });

    const result = await (engine as any).interceptCompaction(interceptRequest({
      signal: { aborted: true },
    }));

    expect(result).toEqual({
      handled: false,
      reason: "aborted-pre-compaction",
    });
    expect(client.commitSession).not.toHaveBeenCalled();
    expect(client.getSessionContext).not.toHaveBeenCalled();
  });

  it("declines bypassed sessions without committing", async () => {
    const { engine, client } = makeEngine({
      status: "completed",
      archived: true,
      memories_extracted: {},
    }, {
      configOverrides: {
        bypassSessionPatterns: ["agent:*:cron:**"],
      },
    });

    const result = await (engine as any).interceptCompaction(interceptRequest({
      sessionId: "runtime-session",
      sessionKey: "agent:main:cron:nightly:run:1",
    }));

    expect(result).toEqual({
      handled: false,
      reason: "session-bypassed",
    });
    expect(client.commitSession).not.toHaveBeenCalled();
  });

  it("falls back with a compact-failed reason when OV commit fails", async () => {
    const { engine } = makeEngine({
      status: "failed",
      error: "extractor failed",
      task_id: "task-failed",
      archived: false,
    });

    const result = await (engine as any).interceptCompaction(interceptRequest());

    expect(result).toEqual({
      handled: false,
      reason: "compact-failed:commit_failed",
    });
  });

  it("falls back when compact produces no archive instead of returning an empty summary", async () => {
    const { engine } = makeEngine({
      status: "completed",
      archived: false,
      task_id: "task-no-archive",
      memories_extracted: {},
    });

    const result = await (engine as any).interceptCompaction(interceptRequest());

    expect(result).toEqual({
      handled: false,
      reason: "compact-failed:commit_no_archive",
    });
  });

  it("falls back when post-compaction assemble produces no context", async () => {
    const { engine } = makeEngine({
      status: "completed",
      archived: true,
      archive_uri: "ov://archive/archive-empty",
      memories_extracted: {},
    });

    const result = await (engine as any).interceptCompaction(interceptRequest());

    expect(result).toEqual({
      handled: false,
      reason: "openviking-produced-no-context",
    });
  });

  it("serializes OV assembled context as lossless-style role blocks and preserves Pi boundaries", async () => {
    const { engine } = makeEngine({
      status: "completed",
      archived: true,
      archive_uri: "ov://archive/ov-archive-99",
      memories_extracted: { core: 2 },
    }, {
      sessionContext: {
        latest_archive_overview: "<summary id=\"ov-1\" kind=\"leaf\">Archived facts</summary>",
        latest_archive_id: "ov-archive-99",
        pre_archive_abstracts: [],
        messages: [{
          id: "assistant-tool",
          role: "assistant",
          parts: [
            { type: "text", text: "I will inspect the file." },
            {
              type: "tool",
              tool_id: "toolu_123",
              tool_name: "read_file",
              tool_input: { path: "README.md" },
              tool_output: "README contents",
              tool_status: "completed",
            },
          ],
          created_at: "2026-05-28T10:00:00.000Z",
        }],
        estimatedTokens: 456,
        stats: { totalArchives: 1, includedArchives: 1, droppedArchives: 0, failedArchives: 0, activeTokens: 200, archiveTokens: 256 },
      },
    });

    const result = await (engine as any).interceptCompaction(interceptRequest({
      firstKeptEntryId: "pi-kept-entry",
      tokensBefore: 7777,
    }));

    expect(result).toMatchObject({
      handled: true,
      firstKeptEntryId: "pi-kept-entry",
      tokensBefore: 7777,
    });
    expect(result.details.openvikingFirstKeptEntryId).toBe("ov-archive-99");
    expect(result.summary).toContain("[user]\n[Session History Summary]\n<summary id=\"ov-1\"");
    expect(result.summary).toContain("[assistant]\nI will inspect the file.");
    expect(result.summary).toContain("[toolCall: read_file {\"path\":\"README.md\"}]");
    expect(result.summary).toContain("[toolResult]\nREADME contents");
  });
});
