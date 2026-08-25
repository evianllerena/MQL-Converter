# MQL ONE v5.4 — MQ5 Trust Intake and Parallel Scale

## New MQ5 Validation section

Existing `.mq5` source can now be added independently of the MQ4 converter.
Each file is copied to a quarantine workspace and receives:

1. Static capability scan before execution.
2. Structural indicator checks (`OnCalculate`, mapped buffers, price/series
   inputs, and likely placeholder output).
3. Isolated MetaEditor compilation.
4. A JSON evidence report under `OUTPUT\_validation\reports`.
5. A clear `runtime NOT TESTED` status until an isolated Strategy Tester run
   is available.

Source containing DLL imports, process execution, networking, trading calls,
or other high-risk capabilities is quarantined even if it compiles. Files that
pass structural/static checks and compilation are copied to
`OUTPUT\VALIDATED_MQ5`.

`MQ5_VERIFIED` means **static and compile verified**, not formula-correct or
safe for a live account. MQL ONE does not execute unknown source in the user's
normal trading terminal.

## Why formula correctness cannot be inferred generically

An indicator can compile, load, and emit changing numbers while implementing
the wrong formula. Proving price correctness requires at least one of:

- a trusted original EX5/MQ4 implementation for golden-output comparison;
- an authoritative formula/specification and expected test vectors; or
- user-approved visual and signal expectations.

Without one of these references, automated runtime testing can prove health
(handle creation, calculated bars, nonempty/finite buffers, stability, and
repainting characteristics) but not author intent.

## MetaTrader-aligned runtime design

The next runtime gate should run in a disposable MT5 test installation with
live trading and DLL imports disabled. A generated EA should load each accepted
indicator with a constant `iCustom` path, wait for `BarsCalculated`, inspect
buffers with `CopyBuffer`, and repeat across symbols/timeframes/history ranges.
MetaTrader supports command-line tester configuration through `terminal64.exe
/config:<file>` and local/remote tester agents.

## Speed changes

- Four compile/deterministic-repair workers by default.
- Two Codex workers by default.
- Worker counts are configurable in the scrollable Settings page.
- Backlog ETA accounts for Codex worker waves.
- Existing zero-second Codex cooldown remains the default.

Parallel work remains compile-gated: every candidate must independently pass
MetaEditor before delivery. Start with 4 compile workers and 2 Codex workers.
If the Codex account or computer becomes rate/resource limited, reduce the
worker count rather than adding a cooldown.

## Research references

- MetaTrader 5 Strategy Tester:
  https://www.metatrader5.com/en/terminal/help/algotrading/testing
- MetaTrader command-line and `[Tester]` configuration:
  https://www.metatrader5.com/en/terminal/help/start_advanced/start
- MQL5 `IndicatorCreate` and `#property tester_indicator`:
  https://www.mql5.com/en/docs/series/indicatorcreate
- MQL5 `BarsCalculated`:
  https://www.mql5.com/en/docs/series/barscalculated
- MQL5 `CopyBuffer`:
  https://www.mql5.com/en/docs/series/copybuffer
- MQL5Unit indicator/EA testing framework:
  https://github.com/RichieLoco/MQL5Unit
- MTUnit automation framework:
  https://github.com/rodrigoshaller/MTUnit
- EA31337 reusable MQL framework:
  https://github.com/EA31337/EA31337-classes
