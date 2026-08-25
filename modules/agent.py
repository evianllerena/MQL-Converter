#!/usr/bin/env python3
"""Isolated Codex CLI repair worker for MQL ONE.

The worker never edits the delivered source directly.  It gives Codex a copy
and the current MetaEditor log, recompiles the candidate, and promotes it only
when the compiler error count improves.  A zero-error candidate is the only
result reported as compiled.
"""
from __future__ import annotations
import json, os, queue, shutil, subprocess, threading, time
from pathlib import Path

MODULE_VERSION = "1.4.0"


def find_codex(hint=""):
    if hint:
        p=Path(hint)
        if p.is_file(): return p
    for name in ("codex.exe", "codex.cmd", "codex"):
        p=shutil.which(name)
        if p: return Path(p)
    return None


def _safe(name):
    s="".join(c if c.isalnum() or c in "._-" else "_" for c in name)
    return (s.strip("._") or "indicator")[:90]


def _argv(codex, output_file, model="", reasoning_effort=""):
    args=[str(codex), "exec", "--ephemeral", "--skip-git-repo-check",
          "--sandbox", "workspace-write", "--json"]
    if model: args += ["--model",str(model)]
    if reasoning_effort: args += ["--config",f'model_reasoning_effort="{reasoning_effort}"']
    args += ["-o", str(output_file), "-"]
    if os.name == "nt" and Path(codex).suffix.lower() in (".cmd", ".bat"):
        return [os.environ.get("ComSpec", "cmd.exe"), "/d", "/c"] + args
    return args


def _prompt(attempt):
    return f"""You are repairing one automatically converted MQL5 custom indicator.
Work only in this directory. Read SOURCE.mq5 and compiler.log. Edit SOURCE.mq5
in place to remove the reported MetaEditor 5 compilation errors while
preserving indicator behavior, inputs, buffers, plots, and calculations.

Hard rules:
- Make the smallest semantically correct MQL5 change.
- Do not create fake dependency stubs or delete indicator logic.
- Do not replace calculations with constants, empty functions, or placeholders.
- Do not claim success; the parent program independently recompiles your file.
- Do not edit compiler.log or any other file.
- MQL5 indicator APIs return handles and values require CopyBuffer.
- Preserve OnInit/OnCalculate/OnDeinit contracts and valid return types.
- Read repair_memory.md when present. It contains compile-verified historical
  repairs. Use it as evidence, but never apply a patch blindly.

This is repair attempt {attempt}. When finished, briefly summarize the edits.
"""


def _event_summary(obj):
    typ=obj.get("type","event"); item=obj.get("item") or {}
    it=item.get("type","")
    if typ=="thread.started": return "Codex session started"
    if typ=="turn.started": return "Codex is analyzing the indicator and compiler log"
    if typ=="turn.completed": return "Codex turn completed"
    if typ=="turn.failed": return "Codex turn failed"
    if typ=="error": return "Codex error: "+str(obj.get("message") or obj.get("error") or "unknown")[:500]
    if typ.startswith("item."):
        if it=="command_execution": return f"Command: {item.get('command','')} [{item.get('status','')}]"[:700]
        if it=="file_change": return "Editing candidate source: "+str(item.get("changes") or item.get("path") or "SOURCE.mq5")[:500]
        if it=="agent_message": return "Codex: "+str(item.get("text") or "")[:700]
        if it=="reasoning": return "Codex reasoning step completed"
        return f"{typ}: {it or 'work item'}"
    return typ

def repair_one(codex, source, log_text, compile_fn, meta, diag_dir,
               baseline_errors, attempt=1, timeout=900, progress_cb=None,
               memory_text="", model="", reasoning_effort=""):
    source=Path(source)
    root=Path(diag_dir).parent / "_agent_jobs" / _safe(source.stem)
    root.mkdir(parents=True, exist_ok=True)
    candidate=root / "SOURCE.mq5"
    log_file=root / "compiler.log"
    message_file=root / "agent_message.txt"
    events_file=root / "agent_events.jsonl"
    shutil.copy2(source, candidate)
    log_file.write_text(log_text or "No compiler log was supplied.", encoding="utf-8")
    (root/"repair_memory.md").write_text(memory_text or
        "No matching compile-verified repairs were found.",encoding="utf-8")
    before=candidate.read_bytes()
    started=time.time()
    try:
        creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0) if os.name=="nt" else 0
        startupinfo=None
        if os.name=="nt":
            startupinfo=subprocess.STARTUPINFO()
            startupinfo.dwFlags |= getattr(subprocess,"STARTF_USESHOWWINDOW",1)
            startupinfo.wShowWindow=getattr(subprocess,"SW_HIDE",0)
        argv=_argv(codex,message_file,model,reasoning_effort)
        proc=subprocess.Popen(argv,stdin=subprocess.PIPE,
             stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,cwd=root,
             errors="replace",bufsize=1,creationflags=creationflags,startupinfo=startupinfo)
        proc.stdin.write(_prompt(attempt)); proc.stdin.close()
        q=queue.Queue(); stderr=[]
        def pump(stream,kind):
            for line in iter(stream.readline,""): q.put((kind,line))
            q.put((kind,None))
        threading.Thread(target=pump,args=(proc.stdout,"stdout"),daemon=True).start()
        threading.Thread(target=pump,args=(proc.stderr,"stderr"),daemon=True).start()
        closed=set(); raw=[]; last_beat=0
        while proc.poll() is None or len(closed)<2:
            if time.time()-started>max(60,int(timeout)):
                proc.kill(); raise subprocess.TimeoutExpired(argv,timeout)
            try: kind,line=q.get(timeout=.25)
            except queue.Empty: kind=line=None
            if line is None:
                if kind: closed.add(kind)
            elif kind=="stderr":
                stderr.append(line)
                if progress_cb and line.strip(): progress_cb("stderr",line.strip()[-700:])
            else:
                raw.append(line); events_file.parent.mkdir(parents=True,exist_ok=True)
                with open(events_file,"a",encoding="utf-8") as fh: fh.write(line)
                try: obj=json.loads(line); summary=_event_summary(obj)
                except Exception: summary=line.strip()[:700]
                if progress_cb and summary: progress_cb("event",summary)
            if progress_cb and time.time()-last_beat>=1:
                progress_cb("heartbeat",f"Codex running — {int(time.time()-started)}s elapsed")
                last_beat=time.time()
        rc=proc.wait(); cp_stderr="".join(stderr); cp_stdout="".join(raw)
    except subprocess.TimeoutExpired:
        return {"status":"FAILED", "errors":baseline_errors,
                "detail":f"Codex timed out after {timeout}s", "elapsed":timeout}
    except Exception as ex:
        return {"status":"FAILED", "errors":baseline_errors,
                "detail":f"Codex launch failed: {type(ex).__name__}: {ex}",
                "elapsed":round(time.time()-started,2)}
    if rc != 0:
        msg=(cp_stderr or cp_stdout or "")[-500:].replace("\n"," ")
        return {"status":"FAILED", "errors":baseline_errors,
                "detail":f"Codex exited {rc}: {msg}",
                "elapsed":round(time.time()-started,2)}
    if not candidate.exists() or candidate.read_bytes() == before:
        return {"status":"NO_CHANGE", "errors":baseline_errors,
                "detail":"Codex completed without changing SOURCE.mq5",
                "elapsed":round(time.time()-started,2)}

    if progress_cb: progress_cb("verify","MetaEditor is independently compiling the Codex candidate")
    result=compile_fn(meta, candidate, timeout=90, diag_dir=diag_dir)
    errors=result.get("errors")
    errors=0 if result.get("ok") else (errors if errors is not None else 999999)
    if result.get("ok"):
        shutil.copy2(candidate, source)
        ex5=candidate.with_suffix(".ex5")
        if ex5.exists(): shutil.copy2(ex5, source.with_suffix(".ex5"))
        summary=message_file.read_text(encoding="utf-8",errors="replace") if message_file.exists() else ""
        return {"status":"COMPILED", "errors":0,
                "detail":f"Codex repaired and MetaEditor verified on attempt {attempt}",
                "elapsed":round(time.time()-started,2),"summary":summary[:12000],
                "before":before.decode("utf-8",errors="replace"),
                "after":candidate.read_text(encoding="utf-8",errors="replace"),
                "events_path":str(events_file)}
    if errors < int(baseline_errors if baseline_errors is not None else 999999):
        shutil.copy2(candidate, source)
        return {"status":"IMPROVED", "errors":errors,
                "detail":f"Codex reduced errors {baseline_errors}->{errors}",
                "elapsed":round(time.time()-started,2)}
    return {"status":"REJECTED", "errors":baseline_errors,
            "detail":f"Codex candidate rejected ({errors} errors; baseline {baseline_errors})",
            "elapsed":round(time.time()-started,2)}
