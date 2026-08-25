#!/usr/bin/env python3
"""modules/compile.py - MetaEditor 5 adapter (v1.4.0).

FIELD FIX HISTORY
  v1.2.0  compile_ex() was EMPTY -> MetaEditor never launched ("NO LOG WRITTEN").
  v1.3.0  Implemented the adapter, BUT passed the command as a Python list.
          On Windows, paths containing a space (e.g. "F:\\hugos way settings\\")
          made subprocess wrap the WHOLE token in quotes:
              "/compile:F:\\hugos way settings\\...\\file.mq5"
          MetaEditor then can't see the /compile switch, so it launches, parses
          nothing, exits rc=0, and writes no log / no .ex5 -> every file failed.
  v1.4.0  THIS BUILD:
          - Builds a properly quoted command STRING on Windows so the quotes sit
            around the PATH VALUE only:  /compile:"<path>"  (MetaEditor-correct).
          - Compiles a SANITIZED copy (ascii, no spaces / #, !, (, &, [) in a
            dedicated _compile workspace, then copies the .ex5 back to the real
            output name. This neutralises pathological source filenames AND, via
            8.3 short-path resolution, the space in the base folder.
          - Falls back through several flag forms and records the EXACT command
            string used, so the trace is unambiguous.

Public API is unchanged:
  compile_one(meta, src, timeout, diag_dir=None) -> (ok, errors, warnings, log)
  compile_ex(meta, src, timeout, diag_dir=None)  -> rich dict
  find_metaeditor(hint) -> Path | None
  is_probably_mt4(meta_path) -> bool
"""
from __future__ import annotations
import json, os, shutil, subprocess, sys, time, traceback
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from textio import read_metaeditor_log, count_errors

MODULE_VERSION = "1.4.0"
_IS_WIN = (os.name == "nt")


def is_probably_mt4(meta_path) -> bool:
    if not meta_path:
        return False
    p = Path(meta_path)
    name = p.name.lower()
    parent = str(p.parent).lower()
    if name == "metaeditor64.exe":
        return False
    if "metatrader 4" in parent or "\\mt4" in parent or "/mt4" in parent:
        return True
    if name == "metaeditor.exe":
        return not ("metatrader 5" in parent or "\\mt5" in parent or "/mt5" in parent)
    return False


def find_metaeditor(hint: str = ""):
    if hint and Path(hint).is_file() and not is_probably_mt4(hint):
        return Path(hint)
    cands = []
    for pf in (os.environ.get("ProgramFiles", r"C:\Program Files"),
               os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
        if not pf or not Path(pf).exists():
            continue
        try:
            for root, _dirs, files in os.walk(pf):
                for n in files:
                    if n.lower() == "metaeditor64.exe":
                        cands.append(Path(root) / n)
        except OSError:
            pass
    if cands:
        return sorted(cands, key=lambda p: ("metatrader 5" not in str(p).lower(),
                                            len(str(p))))[0]
    if hint and Path(hint).is_file():
        return Path(hint)
    return None


def _short_path(p: Path) -> str:
    """Return an 8.3 short path on Windows (no spaces) when available."""
    sp = str(p)
    if not _IS_WIN:
        return sp
    try:
        import ctypes
        from ctypes import wintypes
        _GSPN = ctypes.windll.kernel32.GetShortPathNameW
        _GSPN.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        _GSPN.restype = wintypes.DWORD
        buf = ctypes.create_unicode_buffer(4096)
        n = _GSPN(sp, buf, 4096)
        if n and buf.value:
            return buf.value
    except Exception:
        pass
    return sp


def _safe_stem(stem: str) -> str:
    keep = "".join(c if (c.isalnum() or c in "._-") else "_" for c in stem)
    keep = keep.strip("._") or "src"
    return keep[:80]


def _candidate_logs(src: Path, explicit: Path):
    items = [explicit, src.with_suffix(".log"),
             src.parent / (src.stem + ".log"),
             src.with_suffix(src.suffix + ".log")]
    out = []
    for p in items:
        if p not in out:
            out.append(p)
    return out


def _write_trace(diag_dir, src, payload):
    if not diag_dir:
        return
    try:
        td = Path(diag_dir) / "compile_traces"
        td.mkdir(parents=True, exist_ok=True)
        safe = _safe_stem(Path(src).stem)
        stamp = time.strftime("%Y%m%d_%H%M%S") + "_" + str(time.time_ns())[-6:]
        (td / f"{safe}_{stamp}.json").write_text(
            json.dumps(payload, indent=2, default=str), encoding="utf-8")
    except Exception:
        pass


def _run(meta_short, work_short, log_short, timeout, use_explicit_log):
    """Run MetaEditor with a MetaEditor-correct, quoted command STRING.
    Returns (rc, stdout, stderr, command_string)."""
    if use_explicit_log:
        cmd = f'"{meta_short}" /compile:"{work_short}" /log:"{log_short}"'
    else:
        cmd = f'"{meta_short}" /compile:"{work_short}" /log'
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if _IS_WIN:
        cp = subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                            timeout=max(5, int(timeout)), creationflags=flags)
    else:
        # POSIX (tests): emulate by splitting into a list.
        import shlex
        cp = subprocess.run(shlex.split(cmd), capture_output=True, text=True,
                            errors="replace", timeout=max(5, int(timeout)))
    return cp.returncode, (cp.stdout or "")[-12000:], (cp.stderr or "")[-12000:], cmd


def compile_ex(meta, src, timeout: int = 60, diag_dir=None) -> dict:
    src = Path(src).resolve()
    res = {"ok": False, "errors": None, "warnings": None, "log_text": "",
           "reason": "", "rc": None, "log_path": "", "log_bytes": 0,
           "ex5_path": str(src.with_suffix(".ex5")), "ex5_exists": False,
           "elapsed_seconds": 0.0, "command": "", "attempts_cmds": [],
           "stdout": "", "stderr": "", "searched_logs": [],
           "work_file": "", "exception": ""}
    started = time.time()
    meta = Path(meta).resolve() if meta else None
    workspace = None
    try:
        if not meta or not meta.is_file():
            res["reason"] = "MetaEditor executable not found"
            return res
        if is_probably_mt4(meta):
            res["reason"] = ("Configured compiler looks like MetaEditor 4. "
                             "MT4 cannot compile .mq5 - use MetaTrader 5's "
                             "metaeditor64.exe.")
            return res
        if not src.is_file():
            res["reason"] = f"Source file not found: {src}"
            return res
        if src.suffix.lower() != ".mq5":
            res["reason"] = f"Expected an .mq5 source file, received: {src.name}"
            return res

        # 1) Copy the converted source to a SANITISED, space/special-free name
        #    inside a dedicated workspace. This dodges pathological filenames
        #    (#, !, (, &, [, spaces) that break MetaEditor's command parser.
        base = Path(diag_dir).parent if diag_dir else src.parent
        workspace = base / "_compile"
        workspace.mkdir(parents=True, exist_ok=True)
        safe_name = _safe_stem(src.stem) + ".mq5"
        work = workspace / safe_name
        shutil.copy2(src, work)
        res["work_file"] = str(work)

        work_ex5 = work.with_suffix(".ex5")
        explicit_log = workspace / (_safe_stem(src.stem) + ".log")
        for p in [explicit_log, work_ex5, work.with_suffix(".log")]:
            try:
                if p.exists():
                    p.unlink()
            except OSError:
                pass

        meta_short = _short_path(meta)
        work_short = _short_path(work)
        log_short = _short_path(explicit_log)

        # 2) Attempt with an explicit /log, then a bare /log fallback.
        chosen = None
        for use_explicit in (True, False):
            rc, out, err, cmd = _run(meta_short, work_short, log_short,
                                     timeout, use_explicit)
            res["rc"], res["stdout"], res["stderr"] = rc, out, err
            res["command"] = cmd
            res["attempts_cmds"].append(cmd)
            logs = _candidate_logs(work, explicit_log)
            res["searched_logs"] = [str(p) for p in logs]
            for _ in range(20):
                chosen = next((p for p in logs
                               if p.exists() and p.stat().st_size > 0), None)
                if chosen:
                    break
                time.sleep(0.1)
            if chosen or work_ex5.exists():
                break

        if chosen:
            res["log_path"] = str(chosen)
            res["log_bytes"] = chosen.stat().st_size
            res["log_text"] = read_metaeditor_log(chosen)
            res["errors"], res["warnings"] = count_errors(res["log_text"])
            if diag_dir:
                try:
                    rd = Path(diag_dir) / "raw_logs"
                    rd.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(chosen, rd / (_safe_stem(src.stem) + ".log"))
                except Exception:
                    pass

        # 3) Copy the produced .ex5 back to the real output name.
        real_ex5 = src.with_suffix(".ex5")
        if work_ex5.exists():
            try:
                shutil.copy2(work_ex5, real_ex5)
            except Exception:
                pass
        res["ex5_exists"] = real_ex5.exists()

        res["ok"] = (res["errors"] == 0 and res["ex5_exists"])
        if not chosen and not res["ex5_exists"]:
            res["reason"] = ("MetaEditor ran (rc=%s) but produced no log and no "
                             "EX5. If MetaEditor's window is already OPEN, close "
                             "it (an open editor makes CLI compiles no-op). Also "
                             "confirm this is metaeditor64.exe." % res["rc"])
        elif res["errors"] is None and res["ex5_exists"]:
            res["reason"] = "EX5 produced but log summary unparsed - treating as OK"
            res["ok"] = True
        elif res["errors"] and res["errors"] > 0:
            res["reason"] = f"Compilation reported {res['errors']} error(s)"
        elif not res["ex5_exists"]:
            res["reason"] = "Zero errors reported but no EX5 was created"
        else:
            res["reason"] = "Compiled successfully"
    except subprocess.TimeoutExpired as ex:
        res["reason"] = f"MetaEditor timed out after {timeout}s"
        res["exception"] = repr(ex)
    except Exception as ex:
        res["reason"] = f"Compile adapter exception: {type(ex).__name__}: {ex}"
        res["exception"] = traceback.format_exc()
    finally:
        res["elapsed_seconds"] = round(time.time() - started, 3)
        _write_trace(diag_dir, src, res)
    return res


def compile_one(meta, src, timeout: int = 60, diag_dir=None):
    r = compile_ex(meta, src, timeout, diag_dir)
    log = r["log_text"]
    if not log:
        details = [r.get("reason", ""), f"rc={r.get('rc')}",
                   f"command={r.get('command')}",
                   f"searched_logs={r.get('searched_logs')}",
                   f"stderr={r.get('stderr', '')}"]
        log = "NO LOG WRITTEN | " + " | ".join(
            str(x) for x in details if x not in ("", None, []))
    return r["ok"], r["errors"], r["warnings"], log
