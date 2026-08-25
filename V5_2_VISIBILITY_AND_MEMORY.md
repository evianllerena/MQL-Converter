# MQL ONE v5.2 — Visibility, timing, and repair memory

## Verified input run

`DIAGNOSTICS_20260823_171829.zip` reported 30 files and `DELIVERED=30`.
Twenty-three compiled through the converter/deterministic repair path. Seven
entered Codex repair and were independently verified by MetaEditor.

`DELIVERED` means `COMPILE_VERIFIED`; it does not mean runtime or functional
equivalence to the MT4 indicator.

## Codex Live tab

The new tab displays:

- Current indicator.
- Current Codex state.
- Per-attempt elapsed timer updated every second.
- Exact cooldown countdown until the next permitted agent run.
- Remaining AI queue count.
- Live structured Codex events: session/turn start, commands, candidate edits,
  agent messages, verification, completion, and failures.
- Number of compile-verified fixes stored in repair memory.

The Dashboard backlog ETA now accounts for agent cooldown, queue length, and
observed average Codex duration. It no longer estimates an AI backlog using the
fast deterministic-conversion rate.

## Persistent repair repository

The repository is stored at:

`_work\repair_knowledge.sqlite3`

For every accepted Gen2 or Codex repair it stores:

- Time, indicator, and repair method.
- Normalized MetaEditor error signature.
- Error count before and after.
- Human/agent repair summary.
- Unified source diff.
- Before/after SHA-256 hashes.
- Compile-verification flag.

Only zero-error, MetaEditor-verified changes are learned. Rejected, unchanged,
or merely improved candidates are not treated as proven fixes.

Before a new Codex job, MQL ONE searches up to 500 verified records, ranks
overlapping compiler error families, and supplies the five closest references
in `repair_memory.md`. Codex is explicitly instructed to adapt the evidence
semantically and never apply a historical patch blindly.

The **Repair Memory** tab lists stored fixes. Double-click a record to inspect
its normalized errors, repair summary, and verified patch.

## Diagnostics expansion

Diagnostic bundles now include live JSONL agent events, final agent messages,
per-job compiler logs, and the repair knowledge database.
