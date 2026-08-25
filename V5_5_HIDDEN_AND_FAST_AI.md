# MQL ONE v5.5 — hidden console and faster AI queue

## What was causing the popup

The configured npm launcher is normally `codex.cmd`. Windows must execute a
`.cmd` file through `cmd.exe`, and the worker previously started that process
without Windows' no-window flags. Every Codex job could therefore flash a new
Command Prompt window over the desktop.

v5.5 starts the Codex subprocess with `CREATE_NO_WINDOW`, `STARTF_USESHOWWINDOW`,
and `SW_HIDE`. This applies to normal repair jobs. `BUILD_EXE.bat` intentionally
stays visible during the one-time build so failures are not hidden.

## Faster default queue

The v5.5 defaults are:

- 4 parallel compile workers
- 4 parallel Codex workers
- 0-second Codex cooldown
- `gpt-5.6-luna`
- low reasoning effort

Luna plus low reasoning is the fast supplied profile for repetitive,
well-scoped compiler repairs. Each result is still compiled independently by
MetaEditor. A candidate that does not reduce errors is rejected, and only a
zero-error result is delivered.

Use `gpt-5.6-terra` with medium reasoning for stubborn files that repeatedly
fail the fast profile. Doubling workers does not always halve wall time:
account concurrency, Codex response time, MetaEditor, CPU, antivirus, and disk
speed may become the bottleneck. Measure a representative 100-file batch before
raising the worker counts further.

## How to use it

1. Close the old MQL ONE application.
2. Run `BUILD_EXE.bat` once and use the newly produced `MQL_ONE.exe`.
3. Open Settings and confirm Codex workers = 4, cooldown = 0, model =
   `gpt-5.6-luna`, and reasoning = low.
4. Run a representative batch and export diagnostics. The activity view and
   diagnostics provide the elapsed time needed to identify the next bottleneck.
