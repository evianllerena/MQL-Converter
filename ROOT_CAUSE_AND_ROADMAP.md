# MQL ONE v5.0 — Root-cause report

## v5.4 MQ5 trust intake and parallel execution

v5.4 accepts existing MQ5 source through a separate validation queue. Unknown
source is statically classified, structurally reviewed, compiled in quarantine,
and accompanied by a JSON evidence report. High-risk capability findings are
quarantined even when compilation succeeds. The interface deliberately labels
runtime and formula correctness separately.

Compilation, deterministic repairs, and Codex jobs now run in configurable
parallel worker pools. Defaults are four compile workers and two Codex workers.
The Settings page and large tables now include scrollbars.

## v5.3 control and throughput correction

The 2026-08-23 18:48 diagnostic showed that six agent runs per hour created a
ten-minute delay after every Codex repair. v5.3 defaults this cooldown to zero,
starts queued jobs back-to-back, and adds an explicit Start Compiler button
plus start/finish notifications.

Repair Memory was empty because SQLite was not collected into the one-file
Windows build while the knowledge module was dynamically loaded. v5.3 makes
SQLite an explicit build dependency and logs repository initialization plus
every accepted record.

## Verdict

The MetaEditor adapter is working. `#dss.mq5` compiled and produced an EX5.
The low success rate is caused by incomplete translation, not by MetaEditor.

The previous converter handled MQL4-to-MQL5 as text substitutions plus a small
compatibility shim. That is insufficient because MQL5 indicator functions such
as `iMA`, `iCCI`, `iRSI`, `iATR`, and `iSAR` return handles; MQL4 returned a
calculated value for a requested shift. MQL5 requires `CopyBuffer` to retrieve
that value. Similar semantic differences exist in time, series, object, chart,
order, and custom-indicator APIs.

## Defects proven from the supplied run

1. 29 of 30 test indicators failed compilation; one compiled.
2. The logs contain hundreds of errors, led by wrong parameter counts,
   undeclared MQL4 functions/constants, and cascades from those root errors.
3. Intake used only `Path.stem` as its record key. Repeated filenames in a
   30,000-file tree were silently ignored.
4. The watch tree was recursively rescanned every 120 ms.
5. The ledger was write-only, so restarting forgot the working state.
6. The GUI's AI stage is implemented only in demo mode with random outcomes.
   Real mode places files in `AI_QUEUE` but contains no code to process them.
7. The case-label repair changed variable scope, creating new undeclared-name
   errors such as `TimeFrameStr`.

## Changes in v5.0

- Added MQL4-value adapters using MQL5 handles, `CopyBuffer`, and
  `IndicatorRelease` for MA, CCI, RSI, ATR, and SAR.
- Added time component adapters and Highest/Lowest adapters.
- Added common MQL4 constants and correct `datetime[]` handling for
  `ArrayCopySeries(MODE_TIME, ...)`.
- Corrected bare `Digits` and `Point` conversion.
- Changed switch repair to hoist case declarations without breaking scope.
- Added stable path hashes for duplicate basenames.
- Added ledger reload/resume and throttled large-tree scans.
- Added a conversion smoke test during review and verified all Python modules
  compile successfully.

## Changes in v5.1

- Replaced the stalled AI label with a real isolated Codex CLI worker.
- Added compile-gated promotion: worse candidates are rejected and only
  MetaEditor-verified zero-error candidates are delivered.
- Added a three-attempt ceiling and explicit
  `MANUAL_SEMANTIC_REPAIR_REQUIRED` outcome.
- Added deterministic fixes for void returns, orphan `OnInit` returns, and bare
  `OnCalculate` returns so simple errors do not spend agent calls.
- Corrected diagnostics for an auto-discovered MetaEditor path.

## Changes in v5.2

- Added a live Codex activity tab backed by the official JSONL event stream.
- Added elapsed, cooldown, queue, and schedule-aware backlog timers.
- Added persistent compile-verified repair memory with searchable error
  signatures, summaries, hashes, and unified diffs.
- Added a Repair Memory browser and injected relevant verified history into
  future isolated Codex jobs.
- Expanded diagnostics with Codex events, messages, job logs, and repair memory.

## Changes in v5.5

- Suppressed repeated Windows Command Prompt popups from `codex.cmd` with
  hidden process startup flags and `CREATE_NO_WINDOW`.
- Increased the default Codex worker pool from two to four.
- Added selectable Codex model and reasoning controls in Settings.
- Selected `gpt-5.6-luna` with low reasoning as the fast default while
  preserving the independent MetaEditor compile gate for every candidate.

## Remaining limits

This is a materially better deterministic converter, but no honest tool can
guarantee automatic conversion of 30,000 arbitrary indicators. `iCustom` is
variadic and requires per-indicator buffer knowledge; trading/order code needs
MT5 position/order semantics; missing includes and intentionally malformed or
obfuscated sources require separate handling. Compile success also does not
prove indicator equivalence.

Use staged batches and validate each compiled indicator in MT5 with a fixed
symbol, timeframe, historical range, inputs, buffer values, and visual output.
Do not run all 30,000 until a representative 100-file acceptance batch reaches
an acceptable compile and equivalence rate.

## Build and run

1. Keep `MQL_ONE_app.py` beside the `modules` folder.
2. Run `BUILD_EXE.bat` on the Windows machine that has Python installed.
3. Select MT5's `metaeditor64.exe`, never MT4's `metaeditor.exe`.
4. Start with a representative 100-file batch.
5. Export diagnostics after the batch. Prioritize remaining error families by
   frequency before expanding the next adapter set.
