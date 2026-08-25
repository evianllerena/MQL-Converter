# MQL ONE v5.3 — Start/Finish Control, Speed, and Repair Memory

## What the 2026-08-23 18:48 diagnostic proved

- 30 indicators were delivered and compile-verified.
- Seven indicators required Codex.
- Individual Codex work took approximately 24–199 seconds.
- Six AI runs per hour forced a ten-minute delay between repairs.
- Most of the 68-minute wall-clock run was an artificial cooldown.
- The repair database was absent. The dynamically loaded knowledge module
  depended on SQLite, but SQLite was not collected into the Windows build.

## v5.3 changes

1. The pipeline waits for the physical **START COMPILER** button.
2. The header and logs explicitly announce **COMPILER STARTED**.
3. Completion shows **COMPILER FINISHED**, elapsed time, delivered/blocked
   counts, and a completion dialog.
4. The default Codex cooldown is zero seconds. Jobs start back-to-back.
5. Settings expose optional cooldown and timeout controls.
6. SQLite is explicitly included by `BUILD_EXE.bat`.
7. Repair Memory initialization success or failure is visible in the log.
8. Each accepted verified repair produces a `repair memory recorded` message.

## Expected impact

The seven Codex calls consumed about 6.7 minutes of actual agent time. Removing
the six ten-minute gaps changes this workload from roughly 68 minutes to about
7–9 minutes, depending on MetaEditor and service response time.

Repair Memory begins accumulating with v5.3. The v5.2 diagnostic did not retain
trustworthy before-and-after snapshots, so those records cannot be backfilled
as verified patches.

Compilation verification still does not prove runtime equivalence. Runtime
buffer and visual validation remains a separate test stage.
