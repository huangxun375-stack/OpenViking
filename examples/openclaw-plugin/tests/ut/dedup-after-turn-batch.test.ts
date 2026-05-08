import { describe, expect, it } from "vitest";

import type { OVMessage } from "../../client.js";
import {
  type CapturedOpenVikingMessage,
  capturedMessageSignature,
  deduplicateAfterTurnBatch,
} from "../../context-engine.js";

// Build a CapturedOpenVikingMessage triplet (msg + ovParts + signature)
// directly, so the algorithm-level tests don't have to drag the whole
// engine into setup. The signature here is the same hash the engine
// uses, derived from role + normalized parts, so it cleanly aligns with
// how OVMessage tail entries are signed inside deduplicateAfterTurnBatch.
function makeUserText(text: string): CapturedOpenVikingMessage {
  const ovParts = [{ type: "text" as const, text }];
  return {
    msg: { role: "user", parts: ovParts },
    ovParts,
    signature: capturedMessageSignature({ role: "user", parts: ovParts }),
  };
}

function makeAssistantText(text: string): CapturedOpenVikingMessage {
  const ovParts = [{ type: "text" as const, text }];
  return {
    msg: { role: "assistant", parts: ovParts },
    ovParts,
    signature: capturedMessageSignature({ role: "assistant", parts: ovParts }),
  };
}

function makeStoredFromCaptured(captured: CapturedOpenVikingMessage[]): OVMessage[] {
  return captured.map((c, idx) => ({
    id: `stored_${idx + 1}`,
    role: c.msg.role,
    parts: c.ovParts,
    created_at: "2026-05-07T00:00:00.000Z",
  }));
}

describe("deduplicateAfterTurnBatch — algorithm-level coverage", () => {
  it("no-op when incoming is empty", () => {
    const stored = makeStoredFromCaptured([makeUserText("hi"), makeAssistantText("hello")]);
    const result = deduplicateAfterTurnBatch(stored, []);
    expect(result.matchKind).toBe("no-op");
    expect(result.toAppend).toEqual([]);
    expect(result.skipped).toBe(0);
  });

  it("ingests entire batch when stored tail is empty", () => {
    const incoming = [makeUserText("first"), makeAssistantText("answer")];
    const result = deduplicateAfterTurnBatch([], incoming);
    expect(result.matchKind).toBe("none");
    expect(result.skipped).toBe(0);
    expect(result.toAppend).toBe(incoming);
  });

  it("slices full-prefix replay when stored is exactly the leading prefix of incoming", () => {
    const sharedUser = makeUserText("first user prompt");
    const sharedAssistant = makeAssistantText("first assistant reply");
    const newAssistant = makeAssistantText("final answer this turn");

    const stored = makeStoredFromCaptured([sharedUser, sharedAssistant]);
    const incoming = [sharedUser, sharedAssistant, newAssistant];

    const result = deduplicateAfterTurnBatch(stored, incoming);
    expect(result.matchKind).toBe("full-prefix");
    expect(result.skipped).toBe(2);
    expect(result.toAppend).toEqual([newAssistant]);
  });

  it("falls back to suffix matching when boundary signature does not align", () => {
    // Stored history ends with assistant; incoming carries the same
    // assistant-as-suffix plus a brand new follow-up assistant. The
    // boundary at incoming[storedLength-1] does not equal stored.last,
    // so the routine must take the suffix-fallback branch.
    const storedAssistant = makeAssistantText("response after restart");
    const newUser = makeUserText("follow up question");
    const stored = makeStoredFromCaptured([storedAssistant]);
    const incoming = [newUser, storedAssistant];

    const result = deduplicateAfterTurnBatch(stored, incoming);
    expect(result.matchKind).toBe("none");
    // Suffix fallback ran (context="prefix-mismatch") but found no
    // qualifying overlap — single-stored-message means matchLen=1 and
    // newSlice would be empty, which the fallback rejects to avoid a
    // coincidental single-message match. We then fall through to the
    // ingest policy, surfacing the "<context>-no-overlap-ingest" tag.
    expect(result.reason).toBe("prefix-mismatch-no-overlap-ingest");
    expect(result.toAppend).toEqual(incoming);
  });

  it("oversized + tail-match: keeps incoming when it exactly equals the stored tail", () => {
    const u = makeUserText("turn 1 user");
    const a = makeAssistantText("turn 1 assistant");
    const u2 = makeUserText("turn 2 user");
    const a2 = makeAssistantText("turn 2 assistant");
    const stored = makeStoredFromCaptured([u, a, u2, a2]);
    // Content alone cannot distinguish a tail-only replay from a real
    // next turn that happens to have the same user/assistant text.
    const incoming = [u2, a2];

    const result = deduplicateAfterTurnBatch(stored, incoming);
    expect(result.matchKind).toBe("none");
    expect(result.reason).toBe("tail-match-empty-no-dedup");
    expect(result.skipped).toBe(0);
    expect(result.toAppend).toEqual(incoming);
  });

  it("single-message incoming is preserved instead of content-deduping against older tail", () => {
    // Reviewer-reported regression: stored=[user='same answer', assistant='reply']
    // and incoming=[user='same answer'] — the second turn carries a
    // legitimately new user message whose content happens to match an
    // older stored user. Stored.last is the assistant, suffix-fallback
    // finds no match, and our default "ingest" policy preserves the new
    // user instead of fail-closing it.
    const turn1User = makeUserText("same answer");
    const turn1Assistant = makeAssistantText("first reply");
    const turn2User = makeUserText("same answer");
    const stored = makeStoredFromCaptured([turn1User, turn1Assistant]);
    const incoming = [turn2User];

    const result = deduplicateAfterTurnBatch(stored, incoming);
    expect(result.matchKind).toBe("none");
    expect(result.reason).toBe("single-message-no-dedup");
    expect(result.skipped).toBe(0);
    expect(result.toAppend).toEqual([turn2User]);
  });

  it("single-message incoming ignores explicit skip override to avoid dropping real user input", () => {
    const turn1User = makeUserText("same answer");
    const turn1Assistant = makeAssistantText("first reply");
    const turn2User = makeUserText("same answer");
    const stored = makeStoredFromCaptured([turn1User, turn1Assistant]);
    const incoming = [turn2User];

    const result = deduplicateAfterTurnBatch(stored, incoming, {
      oversizedNoOverlap: "skip",
    });
    expect(result.matchKind).toBe("none");
    expect(result.reason).toBe("single-message-no-dedup");
    expect(result.skipped).toBe(0);
    expect(result.toAppend).toEqual([turn2User]);
  });

  it("does not use a one-message stored prefix as proof for a repeated user plus assistant turn", () => {
    const turn1User = makeUserText("same answer");
    const turn2User = makeUserText("same answer");
    const turn2Assistant = makeAssistantText("second reply");
    const stored = makeStoredFromCaptured([turn1User]);
    const incoming = [turn2User, turn2Assistant];

    const result = deduplicateAfterTurnBatch(stored, incoming);
    expect(result.matchKind).toBe("none");
    expect(result.reason).toBe("single-stored-prefix-no-dedup");
    expect(result.skipped).toBe(0);
    expect(result.toAppend).toEqual(incoming);
  });

  it("oversized with partial suffix overlap: returns the genuinely new tail", () => {
    // Stored ends with [oldUser, oldAssistant]. Incoming reuses
    // oldAssistant as its leading message and carries a new user/
    // assistant pair after. Suffix scan should align on oldAssistant and
    // return only the new tail.
    const oldUser = makeUserText("old user");
    const oldAssistant = makeAssistantText("old assistant");
    const newUser = makeUserText("new user");
    const newAssistant = makeAssistantText("new assistant");

    const stored = makeStoredFromCaptured([oldUser, oldAssistant]);
    const incoming = [oldAssistant, newUser, newAssistant];

    const result = deduplicateAfterTurnBatch(stored, incoming);
    expect(result.matchKind).toBe("suffix-fallback");
    expect(result.toAppend).toEqual([newUser, newAssistant]);
    expect(result.skipped).toBe(1);
  });

  it("does not falsely match a single-message incoming against an unrelated stored last", () => {
    const stored = makeStoredFromCaptured([
      makeUserText("totally unrelated stored content"),
      makeAssistantText("equally unrelated reply"),
    ]);
    const incoming = [makeUserText("brand new user input")];

    const result = deduplicateAfterTurnBatch(stored, incoming);
    expect(result.matchKind).toBe("none");
    expect(result.reason).toBe("single-message-no-dedup");
    expect(result.toAppend).toEqual(incoming);
  });

  it("falls back to suffix matching when boundary matches but the full prefix does not", () => {
    const storedUser = makeUserText("stored user");
    const storedAssistant = makeAssistantText("stored assistant");
    const unrelatedUser = makeUserText("unrelated leading user");
    const finalAssistant = makeAssistantText("final assistant");
    const stored = makeStoredFromCaptured([storedUser, storedAssistant]);
    const incoming = [unrelatedUser, storedAssistant, storedUser, storedAssistant, finalAssistant];

    const result = deduplicateAfterTurnBatch(stored, incoming);
    expect(result.matchKind).toBe("suffix-fallback");
    expect(result.reason).toBe("full-prefix-mismatch");
    expect(result.skipped).toBe(4);
    expect(result.toAppend).toEqual([finalAssistant]);
  });

  it("does not let suffix fallback consume the entire incoming batch when no new tail remains", () => {
    const storedUser = makeUserText("stored user");
    const storedAssistant = makeAssistantText("stored assistant");
    const storedToolResult = makeUserText("stored tool result");
    const unrelatedUser = makeUserText("unrelated leading user");
    const stored = makeStoredFromCaptured([storedUser, storedAssistant, storedToolResult]);
    const incoming = [unrelatedUser, storedUser, storedAssistant, storedToolResult];

    const result = deduplicateAfterTurnBatch(stored, incoming);
    expect(result.matchKind).toBe("none");
    expect(result.reason).toBe("prefix-mismatch-no-overlap-ingest");
    expect(result.skipped).toBe(0);
    expect(result.toAppend).toEqual(incoming);
  });

  it("multi-tool finalizer replay: prefix proof skips the entire stored prefix", () => {
    const user = makeUserText("please run the diagnostic tool once");
    const assCall = makeAssistantText("I will run it now.");
    const toolResult = makeUserText("diagnostic result: ok");
    const finalAss = makeAssistantText("The diagnostic finished cleanly.");

    // After the loop hook captured user / assistant_call / toolResult,
    // stored == [user, assCall, toolResult]. The finalizer replays the
    // entire turn including the final assistant.
    const stored = makeStoredFromCaptured([user, assCall, toolResult]);
    const incoming = [user, assCall, toolResult, finalAss];

    const result = deduplicateAfterTurnBatch(stored, incoming);
    expect(result.matchKind).toBe("full-prefix");
    expect(result.skipped).toBe(3);
    expect(result.toAppend).toEqual([finalAss]);
  });

  it("full identical replay (no new tail): keeps incoming because content-only proof is ambiguous", () => {
    const u = makeUserText("u");
    const a = makeAssistantText("a");
    const stored = makeStoredFromCaptured([u, a]);
    const incoming = [u, a];

    const result = deduplicateAfterTurnBatch(stored, incoming);
    expect(result.matchKind).toBe("none");
    expect(result.reason).toBe("full-prefix-empty-no-dedup");
    expect(result.skipped).toBe(0);
    expect(result.toAppend).toEqual(incoming);
  });
});
