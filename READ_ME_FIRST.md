# MQL ONE v5.4 — READ ME FIRST

Version 5.4 accepts both MQ4 conversion work and existing untrusted MQ5 source.
Use the **MQ5 Validation** tab for existing MQ5 files. A compile/static pass is
not the same as runtime or formula verification; review the generated JSON
report before moving any indicator into a live terminal.

## ⛔ The #1 thing that was breaking your run

Your last run failed on **all 26 files** with `NO LOG WRITTEN`. The cause was
in your settings:

```
metaeditor_path = C:/Program Files (x86)/Blueberry Markets MetaTrader 4/metaeditor.exe
```

**That is MetaTrader 4's compiler. It CANNOT compile .mq5 files** — it just
exits silently, producing no log and no .ex5. The pipeline converts your files
to `.mq5`, so it needs **MetaTrader 5's** compiler.

### ✅ Fix
1. **Install MetaTrader 5** (Blueberry Markets offers an MT5 build; any MT5
   works — even a free MetaQuotes demo, no funded account needed). MetaEditor 5
   comes bundled with it.
2. In the app → **Settings** → **MetaEditor**, choose:
   ```
   C:\Program Files\<your broker> MetaTrader 5\metaeditor64.exe
   ```
   Note: **metaeditor64.exe** (MT5), NOT metaeditor.exe (MT4).
3. **Save**, then **restart the app**.

This build now **warns you in red on the Settings tab** the moment you pick an
MT4 editor, and logs a clear warning at startup — so this can't silently bite
you again.

---

## Run it

```
python MQL_ONE_app.py        (or double-click MQL_ONE.exe after BUILD_EXE.bat)
```

Keep `MQL_ONE_app.py` and the `modules\` folder together.

## The 6 stages
Watch → Convert → Compile → Auto-fix → AI fix → Output. Click any card to see
those files; double-click a file for its full history.

## Files in this package
```
MQL_ONE_app.py       the app
BUILD_EXE.bat        makes MQL_ONE.exe
READ_ME_FIRST.md     this file
modules/             the pipeline (textio, convert, repair, compile, intake, diagnostics)
```

## If something still fails
Click **⬇ Export Diagnostics** (top-right). It writes
`DIAGNOSTICS_<time>.zip` into your output folder. Extract it and send back
`SUMMARY.txt`, `ledger.csv`, and one file from `raw_logs\`.

## What was NOT the problem
Conversion works perfectly — all 26 of your files converted cleanly. The only
broken link was the compiler pointing at MT4. Once MT5 is selected, files will
start reaching **Delivered**.
