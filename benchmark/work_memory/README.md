# Work Memory Benchmark

Phase 1 benchmark suite for the `WM + recent tail` architecture used by
OpenClaw + OpenViking.

## Scope

This suite is designed to answer internal work-memory questions that are not
well covered by generic long-context benchmarks:

- can fresh facts be used from recent tail immediately
- can facts hand off from tail to WM after async commit
- do corrected facts override stale facts
- can tool-grounded facts survive commit and compact
- is WM growth visible and budget pressure measurable

Phase 1 currently defines `24` cases across `6` capability groups:

- `tail_recall`
- `wm_durable_recall`
- `correction`
- `tool_grounded_memory`
- `files_context_pruning`
- `growth_control`

## Execution Modes

- `chat_e2e`
  Sends normal turns through the OpenClaw `/v1/responses` path.
- `session_inject`
  Injects exact message or tool payloads into the OV session for deterministic
  tool-grounded scenarios.
- `artifact_whitebox`
  Uses benchmark-visible artifacts plus budget assertions to probe growth and
  merge behavior.

## Checkpoint Semantics

- `after_turn`
  Validate behavior immediately after the prerequisite turns are written. Use
  this for recent-tail checks.
- `after_async_commit`
  Trigger an async OV commit, wait for completion, then validate the handoff
  surface.
- `after_compact`
  Trigger the compact path and validate whether facts remain durable after tail
  pressure has increased.

The runner collects a context snapshot for every checkpoint and writes the
result into the bundle artifacts.

## Expected Failure Policy

Some phase-1 cases are intentionally marked `expected_failure: true`. These
cases document known gaps we want to keep visible instead of hiding.

- `expected_failure` does not remove the case from the suite
- `expected_failure` is still reported in `checkpoints.csv`
- a case should only lose its `expected_failure` flag after repeated successful
  runs and explicit review

## Repeat Policy

Use `--repeat N` to rerun each selected case multiple times.

- `chat_e2e` and `session_inject` use majority pass when `N > 1`
- `artifact_whitebox` uses all-pass when `N > 1`
- the final report keeps `pass_count / repeat_count` per case

## Case Authoring Guide

Each case lives under `benchmark/work_memory/cases/*.yaml`.

Required top-level fields:

- `case_id`
- `title`
- `mode`
- `capability`
- `priority`
- `checkpoints`

Common optional fields:

- `tags`
- `expected_failure`
- `turns`
- `artifact_assertions`
- `budget_assertions`

Supported checkpoint fields:

- `id`
- `trigger`
- `query`
- `expected_any`
- `forbidden_any`
- `source_expectation`

`source_expectation` is diagnostic-only in phase 1. The runner records it in
artifacts and reports for post-hoc analysis, but it does not change routing,
gating, or pass/fail behavior.

Supported artifact assertions:

- `wm_must_contain`
- `wm_must_not_contain`
- `wm_may_contain_only_as_historical`
- `section_contains`
- `section_not_contains`
- `tail_min_messages`
- `tool_pair_count`
- `key_file_paths_present`

Supported budget assertions:

- `max_total_wm_tokens`
- `max_section_tokens`
- `max_section_items`

Example:

```yaml
case_id: correction_current_owner
title: Correction current owner
mode: chat_e2e
capability: correction
priority: critical
turns:
  - role: user
    content: "Remember: the owner is Alice."
  - role: user
    content: "Correction: the owner is Bob."
checkpoints:
  - id: cp_after_turn
    trigger: after_turn
    query: "Who is the owner now?"
    expected_any: ["Bob"]
    forbidden_any: ["Alice"]
    source_expectation: tail
artifact_assertions:
  wm_must_contain:
    - "Bob"
  wm_may_contain_only_as_historical:
    - "Alice"
```

## Output Bundle

Each run writes results under the selected output directory:

```text
summary.json
checkpoints.csv
capability_scores.json
growth.csv
report.md
artifacts/<case_id>/<checkpoint_id>.json
```

## Useful Commands

```powershell
py -3 benchmark\work_memory\run.py --list-cases
py -3 benchmark\work_memory\run.py --case tail_fact_single --dry-run
py -3 benchmark\work_memory\run.py --capability correction --repeat 3 --output-dir benchmark\results\work_memory_smoke_repeat
py -3 -m pytest -q tests\unit\benchmark
```
