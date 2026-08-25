# Set up the real Codex repair queue

MQL ONE uses the official Codex CLI in non-interactive mode. The agent
works in an isolated folder under `OUTPUT\_agent_jobs`; it never edits the
delivered file directly. MQL ONE recompiles each candidate with MetaEditor and
promotes it only when the error count decreases. Only a zero-error MetaEditor
result enters `DELIVERED`.

## One-time setup on Windows

1. Install Node.js LTS if `node --version` does not work.
2. Open PowerShell and install the Codex CLI:

   `npm install -g @openai/codex`

3. Run `codex` once and sign in using the offered ChatGPT or API-key method.
4. Verify `codex --version` works.
5. In MQL ONE → Settings, select `codex.cmd` or `codex.exe`. A common npm path
   is `%APPDATA%\npm\codex.cmd`.
6. Leave **AI cooldown sec** at `0` for back-to-back processing. Increase it
   only if you intentionally need to rate-limit Codex. The timeout defaults to
   360 seconds per repair.

Do not paste an API key into MQL ONE. Codex manages its own authenticated CLI
session. The application invokes `codex exec --ephemeral --sandbox
workspace-write` and sends only the isolated candidate and compiler log.

## Acceptance controls

- Maximum three Codex attempts per file.
- Unchanged or worse candidates are rejected.
- Better-but-not-clean candidates remain queued for another bounded attempt.
- Three unsuccessful attempts become `MANUAL_SEMANTIC_REPAIR_REQUIRED`.
- Missing real includes are not replaced with fake stubs.
- Compile success means `COMPILE_VERIFIED`, not runtime equivalence.

## v5.5 hidden and faster Codex profile

Normal Codex jobs no longer flash a Command Prompt window. On Windows, MQL ONE
still uses `cmd.exe /d /c` when the selected launcher is `codex.cmd`, but starts
that child process with `CREATE_NO_WINDOW` and a hidden startup window. The
one-time `BUILD_EXE.bat` build window remains visible so build errors can be
read; the compiled application and repair workers run without a console.

The defaults are now four compile workers, four Codex workers, zero cooldown,
`gpt-5.6-luna`, and low reasoning. For a difficult indicator, switch the model
to `gpt-5.6-terra` and reasoning to medium. Increasing workers can improve
throughput, but account limits, CPU, disk, and concurrent MetaEditor work can
produce diminishing returns.
