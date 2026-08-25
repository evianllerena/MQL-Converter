#!/usr/bin/env python3
# =====================================================================
#  MQL ONE - desktop application
#  ONE file. ONE window. Native Tkinter (ships with Python - no installs).
#
#  v4.2.1 adds an explicit MT4-vs-MT5 warning: pointing at MetaTrader 4's
#  metaeditor.exe cannot compile .mq5 and was the cause of "NO LOG WRITTEN".
#  The Settings tab now flags a wrong compiler immediately.
# =====================================================================
from __future__ import annotations
import csv, difflib, hashlib, importlib.util, json, os, queue, random, shutil, sqlite3, sys, threading, time, traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    _DND_BASE = TkinterDnD.Tk; _HAS_DND = True
except Exception:
    _DND_BASE = tk.Tk; _HAS_DND = False

APP_NAME    = "MQL ONE"
APP_VERSION = "5.5.0"
BASE        = Path(sys.executable).resolve().parent \
              if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config.json"
MODULES_DIR = BASE / "modules"

BLACK="#0E1013"; WALL="#14161A"; CARD="#1C1F24"; PANEL2="#191C21"
INK="#EAEEF0"; MUTE="#93A1A8"; DIM="#67767E"; LINE="#212326"; LINE2="#303134"
BLUE="#34D4DE"; CYANDP="#12A9B4"; GREEN="#34D4DE"; AMBER="#E6B23A"; RED="#FF6B6B"
BG=BLACK; DARK=BLACK; DARKINK=INK

MODULES = {}; DEMO_MODE = False

def _load_module(name):
    path = MODULES_DIR / f"{name}.py"
    if not path.exists(): return None
    if str(MODULES_DIR) not in sys.path: sys.path.insert(0, str(MODULES_DIR))
    try:
        spec = importlib.util.spec_from_file_location(f"mqlone_{name}", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"mqlone_{name}"] = mod
        spec.loader.exec_module(mod); return mod
    except Exception as ex:
        print(f"[loader] {name} failed: {ex}"); return None

def load_all_modules():
    global DEMO_MODE
    for n in ("textio","convert","repair","compile","intake","diagnostics","agent","knowledge","mq5validate"):
        MODULES[n] = _load_module(n)
    DEMO_MODE = MODULES.get("convert") is None
    return not DEMO_MODE

def read_manifest():
    m = MODULES_DIR / "manifest.json"
    if m.exists():
        try: return json.loads(m.read_text("utf-8"))
        except Exception: pass
    return {n:(getattr(mod,"MODULE_VERSION","—") if mod else "not loaded")
            for n,mod in MODULES.items()} or {"convert":"—"}

def check_for_updates(source_dir):
    if not source_dir or not Path(source_dir).exists():
        return "No update source set. Point it at a folder/share you control."
    import shutil
    src = Path(source_dir); copied = []
    for f in src.glob("*.py"):
        dst = MODULES_DIR / f.name
        try:
            if (not dst.exists()) or f.stat().st_mtime > dst.stat().st_mtime:
                shutil.copy2(f, dst); copied.append(f.name)
        except Exception: pass
    if (src/"manifest.json").exists():
        try: shutil.copy2(src/"manifest.json", MODULES_DIR/"manifest.json")
        except Exception: pass
    load_all_modules()
    return (f"Updated {len(copied)}: {', '.join(copied)} — reloaded."
            if copied else f"Checked {source_dir} — all up to date.")

@dataclass
class Config:
    watch_folder:str=str(BASE/"WATCH_INBOX"); output_folder:str=str(BASE/"OUTPUT")
    metaeditor_path:str=""; claude_path:str=""; codex_path:str=""; extract_zips:bool=True
    repair_passes:int=3; agent_enabled:bool=True; agent_batch:int=10
    agent_cooldown_seconds:int=0; agent_timeout_seconds:int=360; poll_seconds:int=4
    compile_workers:int=4; agent_workers:int=4
    codex_model:str="gpt-5.6-luna"; codex_reasoning_effort:str="low"
    @staticmethod
    def load():
        if CONFIG_PATH.exists():
            try:
                d=json.loads(CONFIG_PATH.read_text("utf-8"))
                return Config(**{k:v for k,v in d.items() if k in Config.__annotations__})
            except Exception: pass
        return Config()
    def save(self): CONFIG_PATH.write_text(json.dumps(asdict(self),indent=2),"utf-8")

STATES=["NEW","MQ5_REVIEW","CONVERTED","COMPILED","NEEDS_REPAIR","AI_QUEUE",
        "DELIVERED","MQ5_VERIFIED","MQ5_REJECTED","BLOCKED"]
STAGE_PCT={"NEW":10,"MQ5_REVIEW":20,"CONVERTED":35,"NEEDS_REPAIR":55,"AI_QUEUE":75,
           "COMPILED":90,"DELIVERED":100,"MQ5_VERIFIED":100,"MQ5_REJECTED":100,
           "BLOCKED":100}
def progress_bar(pct):
    f=int(round(pct/10)); return "\u25b0"*f+"\u25b1"*(10-f)+f"  {pct}%"

@dataclass
class FileRecord:
    name:str; path:str=""; state:str="NEW"; errors:int=0; attempts:int=0
    detail:str=""; history:list=field(default_factory=list); validation:dict=field(default_factory=dict)
    def move(self,ns,detail=""):
        self.history.append((time.strftime("%H:%M:%S"),self.state,ns,detail))
        self.state=ns; self.detail=detail

class Store:
    def __init__(self, ledger_path=None):
        self.records={}; self.lock=threading.Lock(); self.ledger_path=ledger_path
        self._load_ledger()
    def _load_ledger(self):
        """Resume the latest state instead of forgetting all work on restart."""
        if not self.ledger_path or not Path(self.ledger_path).exists(): return
        try:
            with open(self.ledger_path,"r",encoding="utf-8-sig",newline="") as fh:
                for row in csv.DictReader(fh):
                    name=row.get("name","").strip()
                    if not name: continue
                    r=self.records.get(name) or FileRecord(name=name)
                    r.path=row.get("path",r.path); r.state=row.get("state",r.state)
                    r.errors=int(row.get("errors") or 0); r.attempts=int(row.get("attempts") or 0)
                    r.detail=row.get("detail",""); self.records[name]=r
        except Exception:
            # A damaged ledger must not prevent the application from starting.
            self.records={}
    def add(self,name,path="",state="NEW"):
        with self.lock:
            if name not in self.records:
                self.records[name]=FileRecord(name=name,path=path,state=state); return True
            return False
    def move(self,name,ns,detail="",errors=None):
        with self.lock:
            r=self.records.get(name)
            if r:
                if errors is not None: r.errors=errors
                r.move(ns,detail)
        self._append_ledger(name)
    def _append_ledger(self,name):
        if not self.ledger_path: return
        r=self.records.get(name)
        if not r: return
        try:
            new=not Path(self.ledger_path).exists()
            with open(self.ledger_path,"a",encoding="utf-8-sig",newline="") as fh:
                w=csv.writer(fh)
                if new: w.writerow(["time","name","state","errors","attempts","detail","path"])
                w.writerow([time.strftime("%Y-%m-%d %H:%M:%S"),r.name,r.state,
                            r.errors,r.attempts,r.detail,r.path])
        except Exception: pass
    def by_state(self,s):
        with self.lock: return [r for r in self.records.values() if r.state==s]
    def counts(self):
        c={s:0 for s in STATES}
        with self.lock:
            for r in self.records.values(): c[r.state]=c.get(r.state,0)+1
        return c
    def all(self):
        with self.lock: return list(self.records.values())

class Engine(threading.Thread):
    def __init__(self,cfg,store,bus,events_path=None,diag_dir=None):
        super().__init__(daemon=True)
        self.cfg,self.store,self.bus=cfg,store,bus
        self.paused=threading.Event(); self.stopped=threading.Event()
        self.active_card=""; self.processed=0; self.t0=time.time()
        self.meta=None; self._seen=set()
        self._next_scan=0.0
        self._next_agent_run=0.0; self._agent_missing_logged=False
        self.agent_started=0.0; self.agent_file=""; self.agent_state="idle"
        self.agent_last_event=""; self.agent_completed=0
        self.agent_durations=[]
        self.events_path=events_path; self.diag_dir=diag_dir
        self.knowledge=None
        self.run_gate=threading.Event(); self.run_had_work=False
        self.run_started_at=0.0; self.run_number=0; self.run_baseline_counts={}
    def emit(self,kind,**kw): self.bus.put({"kind":kind,**kw})
    def log(self,msg,level="info"):
        self.emit("log",msg=msg,level=level)
        if self.events_path:
            try:
                with open(self.events_path,"a",encoding="utf-8") as fh:
                    fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  [{level}]  {msg}\n")
            except Exception: pass
    def run(self):
        real=load_all_modules()
        if MODULES.get("knowledge") and self.events_path:
            try:
                self.knowledge=MODULES["knowledge"].KnowledgeBase(
                    Path(self.events_path).parent/"repair_knowledge.sqlite3")
                self.log(f"repair memory ready: {self.knowledge.path}","good")
            except Exception as ex:
                self.log(f"repair memory unavailable: {type(ex).__name__}: {ex}","error")
        elif not MODULES.get("knowledge"):
            self.log("repair memory module did not load; rebuild with the v5.3 build script","error")
        self.log(f"engine v{APP_VERSION} ready — click START COMPILER to begin — "
                 f"{'REAL pipeline' if real else 'DEMO (no modules/)'}","good")
        if real and MODULES["compile"]:
            self.meta=MODULES["compile"].find_metaeditor(self.cfg.metaeditor_path)
            if self.meta and not self.cfg.metaeditor_path:
                # Keep diagnostics truthful when auto-discovery found the
                # compiler even though Settings was left blank.
                self.cfg.metaeditor_path=str(self.meta)
            if self.meta and MODULES["compile"].is_probably_mt4(self.meta):
                self.log("WARNING: MetaEditor path looks like MetaTrader 4 "
                         "(metaeditor.exe). MT4 CANNOT compile .mq5 — point "
                         "Settings at MetaTrader 5's metaeditor64.exe.","error")
            else:
                self.log(f"MetaEditor: {self.meta or 'NOT FOUND (compile skipped)'}",
                         "info" if self.meta else "warn")
        self.log(f"watching: {self.cfg.watch_folder}")
        if DEMO_MODE:
            for i in range(1,61): self.store.add(f"Indicator_{i:03d}")
        while not self.stopped.is_set():
            if not self.run_gate.is_set():
                self._tick(); time.sleep(0.25); continue
            if self.paused.is_set(): time.sleep(0.3); continue
            try: self.tick_demo() if DEMO_MODE else self.tick_real()
            except Exception as ex: self.log(f"cycle error: {ex}","error")
            time.sleep(0.4 if DEMO_MODE else 0.12)

    def request_start(self):
        if self.run_gate.is_set(): return False
        self.run_number+=1; self.run_started_at=time.time(); self.t0=self.run_started_at
        self.processed=0; self.run_had_work=False; self._next_agent_run=0.0
        self.run_baseline_counts=self.store.counts()
        self.paused.clear(); self.run_gate.set()
        self.agent_state="ready"; self.agent_last_event="Compiler pipeline started"
        self.log(f"COMPILER RUN #{self.run_number} STARTED","good")
        self.emit("run_started",run_number=self.run_number)
        return True

    def tick_real(self):
        s=self.store; conv=MODULES["convert"]; rep=MODULES["repair"]
        comp=MODULES["compile"]; tio=MODULES["textio"]
        self.intake_real()
        if any(s.by_state(x) for x in ("NEW","MQ5_REVIEW","CONVERTED","NEEDS_REPAIR","AI_QUEUE")):
            self.run_had_work=True
        mq5q=s.by_state("MQ5_REVIEW")
        if mq5q:
            self.active_card="mq5test"; validator=MODULES.get("mq5validate")
            batch=mq5q[:max(1,int(self.cfg.compile_workers))]
            def validate_one(r):
                text=tio.read_source_text(Path(r.path))
                report=validator.analyze(text,r.name) if validator else {
                    "name":r.name,"risk":"unknown","verdict":"REVIEW","quality_score":0,
                    "findings":[],"structural_findings":["validator module unavailable"],
                    "compile":{},"runtime":{"status":"NOT_TESTED"}}
                qdir=Path(self.cfg.output_folder)/"_validation"/"quarantine"
                qdir.mkdir(parents=True,exist_ok=True); candidate=qdir/(r.name+".mq5")
                tio.write_source_text(candidate,text)
                cres=comp.compile_ex(self.meta,candidate,timeout=90,diag_dir=self.diag_dir)
                report["compile"]={"status":"PASS" if cres.get("ok") else "FAIL",
                                   "errors":cres.get("errors"),"warnings":cres.get("warnings"),
                                   "reason":cres.get("reason"),"elapsed_seconds":cres.get("elapsed_seconds")}
                report["runtime"]={"status":"NOT_TESTED",
                    "reason":"Requires generated MT5 Strategy Tester harness and price history"}
                rp=Path(self.cfg.output_folder)/"_validation"/"reports"/(r.name+".json")
                if validator: validator.save_report(report,rp)
                return r,report,candidate,rp
            if self.meta is None:
                for r in batch:
                    s.move(r.name,"MQ5_REJECTED","MQ5 compile not run: MetaEditor not configured")
            else:
                with ThreadPoolExecutor(max_workers=len(batch)) as pool:
                    jobs=[pool.submit(validate_one,r) for r in batch]
                    for fut in as_completed(jobs):
                        try:
                            r,report,candidate,rp=fut.result(); r.validation=report
                            comp_ok=report["compile"]["status"]=="PASS"
                            high_risk=report.get("risk") in ("high","critical")
                            structural=report.get("structural_findings") or []
                            static_pass=report.get("verdict")=="STATIC_PASS"
                            if comp_ok and not high_risk and static_pass:
                                dest=Path(self.cfg.output_folder)/"VALIDATED_MQ5"/(r.name+".mq5")
                                dest.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(candidate,dest)
                                if candidate.with_suffix(".ex5").exists():
                                    shutil.copy2(candidate.with_suffix(".ex5"),dest.with_suffix(".ex5"))
                                r.path=str(dest)
                                detail=(f"STATIC+COMPILE PASS; runtime NOT TESTED; risk={report['risk']}; "
                                        f"quality={report['quality_score']}; report={rp}")
                                s.move(r.name,"MQ5_VERIFIED",detail,errors=0)
                                self.processed+=1; self.log(f"MQ5 STATIC + COMPILE VERIFIED: {r.name}; runtime pending","good")
                            else:
                                why=("high-risk capabilities" if high_risk else
                                     (f"compile failed ({report['compile'].get('errors')} errors)" if not comp_ok
                                      else "structural review required: "+", ".join(structural[:4])))
                                detail=f"QUARANTINED: {why}; report={rp}"
                                s.move(r.name,"MQ5_REJECTED",detail,
                                       errors=report["compile"].get("errors") or 0)
                                self.log(f"MQ5 QUARANTINED: {r.name} — {why}","error")
                        except Exception as ex:
                            self.log(f"MQ5 validation failure: {type(ex).__name__}: {ex}","error")
            self._tick(); return
        if s.by_state("NEW"):
            r=s.by_state("NEW")[0]; self.active_card="convert"
            try:
                src=tio.read_source_text(Path(r.path)) if r.path else ""
                out=conv.convert_source(src)
                od=Path(self.cfg.output_folder); od.mkdir(parents=True,exist_ok=True)
                mq5=od/(r.name+".mq5"); tio.write_source_text(mq5,out); r.path=str(mq5)
                s.move(r.name,"CONVERTED","translated MQL4->MQL5")
                self.log(f"convert: {r.name}.mq4 -> {r.name}.mq5")
            except Exception as ex:
                s.move(r.name,"NEEDS_REPAIR",f"convert error: {ex}")
                self.log(f"convert failed {r.name}: {ex}","error")
            self._tick(); return
        if s.by_state("CONVERTED"):
            batch=s.by_state("CONVERTED")[:max(1,int(self.cfg.compile_workers))]
            self.active_card="compile"
            if self.meta is None:
                for r in batch:
                    s.move(r.name,"NEEDS_REPAIR","no MetaEditor - awaiting compile")
                    self.log(f"no MetaEditor; queued: {r.name}","warn")
                self._tick(); return
            with ThreadPoolExecutor(max_workers=len(batch)) as pool:
                jobs={pool.submit(comp.compile_ex,self.meta,r.path,60,self.diag_dir):r for r in batch}
                for fut in as_completed(jobs):
                    r=jobs[fut]
                    try: res=fut.result()
                    except Exception as ex: res={"ok":False,"errors":None,"reason":str(ex)}
                    if res["ok"]:
                        s.move(r.name,"DELIVERED","compiled clean",errors=0); self.processed+=1
                        self.log(f"compiled clean -> OUTPUT: {r.name}.mq5","good")
                    else:
                        e=res.get("errors"); reason=res.get("reason","compile failed")
                        s.move(r.name,"NEEDS_REPAIR",
                               f"{e if e is not None else '?'} err · {reason}",errors=e or 0)
                        self.log(f"needs repair ({e} errors, {reason}): {r.name}.mq5","warn")
            self._tick(); return
        need=s.by_state("NEEDS_REPAIR")
        if need and self.meta is not None:
            batch=need[:max(1,int(self.cfg.compile_workers))]; self.active_card="repair"
            def repair_one_local(r):
                code=tio.read_source_text(Path(r.path))
                res=comp.compile_ex(self.meta,r.path,diag_dir=self.diag_dir)
                log=res["log_text"]
                if not log.strip():
                    return r,"NO_LOG",None,None,None,None,res,code,log
                status,e,w,ch=rep.repair(r.name,code,log,comp.compile_one,self.meta,
                                         Path(r.path),passes=self.cfg.repair_passes)
                after=tio.read_source_text(Path(r.path)) if status=="REPAIRED_COMPILED" else ""
                return r,status,e,w,ch,after,res,code,log
            with ThreadPoolExecutor(max_workers=len(batch)) as pool:
                jobs={pool.submit(repair_one_local,r):r for r in batch}
                for fut in as_completed(jobs):
                    try:
                        r,status,e,w,ch,after,res,code,log=fut.result()
                        if status=="NO_LOG":
                            s.move(r.name,"AI_QUEUE",f"no compiler log ({res['reason']})")
                            self.log(f"repair blind ({res['reason']}) -> AI queue: {r.name}","warn")
                            continue
                        if status=="REPAIRED_COMPILED":
                            if self.knowledge:
                                try:
                                    saved=self.knowledge.record(r.name,"Gen2 deterministic",log,code,after,
                                                                res.get("errors"),0,"; ".join(ch))
                                    if saved: self.log(f"repair memory recorded: {r.name} (Gen2)","good")
                                except Exception as kex:
                                    self.log(f"repair memory write failed (compile result preserved): {kex}","warn")
                            s.move(r.name,"DELIVERED","Gen2: "+";".join(ch)[:60],errors=0)
                            self.processed+=1; self.log(f"REPAIRED -> compiled: {r.name}.mq5","good")
                        elif status.startswith("BLOCKED_DEPENDENCY"):
                            s.move(r.name,"BLOCKED","missing include: "+status.split(":",1)[-1])
                            self.log(f"blocked (missing include): {r.name}.mq5","error")
                        else:
                            s.move(r.name,"AI_QUEUE",f"Gen2 best {e} err; queued for AI",errors=e or 0)
                            self.log(f"Gen2 exhausted -> AI queue: {r.name}.mq5","warn")
                    except Exception as ex:
                        r=jobs[fut]; s.move(r.name,"AI_QUEUE",f"parallel repair error: {ex}")
                        self.log(f"parallel repair error: {ex}","error")
            self._tick(); return
        queued=s.by_state("AI_QUEUE")
        if queued and self.cfg.agent_enabled:
            agent=MODULES.get("agent")
            codex=agent.find_codex(self.cfg.codex_path or self.cfg.claude_path) if agent else None
            if not codex:
                if not self._agent_missing_logged:
                    self.log("AI queue paused: Codex CLI not found. Install/login to Codex, "
                             "then select codex.exe or codex.cmd in Settings.","error")
                    self._agent_missing_logged=True
                self._tick(); return
            if time.time() < self._next_agent_run:
                self._tick(); return
            self.active_card="aifix"; eligible=[]
            for r in queued:
                if r.attempts>=3:
                    s.move(r.name,"BLOCKED","MANUAL_SEMANTIC_REPAIR_REQUIRED after 3 agent attempts")
                    self.log(f"agent exhausted -> manual repair: {r.name}.mq5","error")
                elif len(eligible)<max(1,int(self.cfg.agent_workers)):
                    r.attempts+=1; eligible.append(r)
            if not eligible: self._tick(); return
            self.agent_started=time.time(); self.agent_file=f"{len(eligible)} parallel job(s)"
            self.agent_state="starting"; self.agent_last_event="Preparing isolated repair workspaces"
            for r in eligible: self.log(f"Codex repair attempt {r.attempts}: {r.name}.mq5")
            def run_codex_job(r):
                current=comp.compile_ex(self.meta,r.path,timeout=90,diag_dir=self.diag_dir)
                baseline=current["errors"] if current["errors"] is not None else max(1,r.errors)
                memory=""
                if self.knowledge:
                    try:
                        refs=self.knowledge.similar(current["log_text"],limit=5)
                        memory=MODULES["knowledge"].render_references(refs)
                        self.log(f"repair memory for {r.name}: {len(refs)} matching reference(s)")
                    except Exception as kex:
                        self.log(f"repair memory lookup failed; continuing without it: {kex}","warn")
                def progress(kind,message):
                    self.agent_state=kind; self.agent_last_event=message
                    self.emit("agent_event",event_kind=kind,message=message,
                              file=r.name,elapsed=time.time()-self.agent_started)
                result=agent.repair_one(codex,Path(r.path),current["log_text"],
                                        comp.compile_ex,self.meta,self.diag_dir,
                                        baseline,r.attempts,progress_cb=progress,
                                        memory_text=memory,
                                        timeout=max(60,int(self.cfg.agent_timeout_seconds)),
                                        model=self.cfg.codex_model,
                                        reasoning_effort=self.cfg.codex_reasoning_effort)
                return r,current,baseline,result
            with ThreadPoolExecutor(max_workers=len(eligible)) as pool:
              jobs={pool.submit(run_codex_job,r):r for r in eligible}
              for fut in as_completed(jobs):
                r=jobs[fut]
                try:
                    r,current,baseline,result=fut.result(); status=result["status"]
                    if result.get("elapsed") is not None:
                        self.agent_durations=(self.agent_durations+[float(result["elapsed"])])[-50:]
                    if status=="COMPILED":
                        if self.knowledge:
                            try:
                                saved=self.knowledge.record(r.name,"Codex",current["log_text"],
                                    result.get("before",""),result.get("after",""),baseline,0,
                                    result.get("summary") or result["detail"])
                                if saved:self.log(f"repair memory recorded: {r.name} (Codex)","good")
                            except Exception as kex:self.log(f"repair memory write failed: {kex}","warn")
                        s.move(r.name,"DELIVERED",result["detail"],errors=0)
                        self.processed+=1; self.agent_completed+=1
                        self.log(f"CODEX + METAEDITOR VERIFIED: {r.name}.mq5","good")
                    elif status=="IMPROVED":
                        s.move(r.name,"AI_QUEUE",result["detail"],errors=result["errors"])
                        self.log(f"agent improved {r.name}: {result['detail']}","warn")
                    elif r.attempts>=3:
                        s.move(r.name,"BLOCKED",result["detail"]+"; MANUAL_SEMANTIC_REPAIR_REQUIRED",
                               errors=result.get("errors",baseline))
                        self.log(f"agent candidate not accepted: {r.name}: {result['detail']}","error")
                    else:
                        s.move(r.name,"AI_QUEUE",result["detail"],errors=result.get("errors",baseline))
                        self.log(f"agent retry needed: {r.name}: {result['detail']}","warn")
                except Exception as ex:
                    s.move(r.name,"AI_QUEUE",f"agent error: {type(ex).__name__}: {ex}")
                    self.log(f"agent error {r.name}: {ex}","error")
            self.agent_state="verified"; self.agent_last_event=f"Parallel Codex batch completed: {len(eligible)} job(s)"
            # No hidden ten-minute throttle. Optional cooldown defaults to zero.
            self._next_agent_run=time.time()+max(0,int(self.cfg.agent_cooldown_seconds))
            self.agent_started=0.0; self.agent_file=""
            self._tick(); return
        self._tick()

    def intake_real(self):
        intake=MODULES["intake"]; wf=Path(self.cfg.watch_folder)
        if not wf.exists(): return
        now=time.time()
        if now < self._next_scan: return
        self._next_scan=now+max(30,int(self.cfg.poll_seconds))
        exts={".mq4",".mq5"}
        for p in intake.scan_watch(wf,exts):
            if str(p) in self._seen: continue
            self._seen.add(str(p))
            # A 30k corpus commonly contains repeated basenames in different
            # folders.  The old dict keyed only by p.stem silently discarded
            # every duplicate.  Preserve the readable name and add a stable
            # path hash only when a collision exists.
            key=p.stem
            old=self.store.records.get(key)
            if old and Path(old.path) != p:
                rel=str(p.relative_to(wf)).replace("\\","/")
                key=f"{p.stem}__{hashlib.sha1(rel.encode('utf-8')).hexdigest()[:10]}"
            state="MQ5_REVIEW" if p.suffix.lower()==".mq5" else "NEW"
            if self.store.add(key,str(p),state=state): self.log(f"intake: staged {p.name}")
        if self.cfg.extract_zips:
            for z in wf.rglob("*.zip"):
                if str(z) in self._seen: continue
                self._seen.add(str(z))
                for f in intake.harvest_zip(z,wf/"_extracted",exts):
                    rel=str(f.relative_to(wf)).replace("\\","/")
                    key=f.stem
                    old=self.store.records.get(key)
                    if old and Path(old.path) != f:
                        key=f"{f.stem}__{hashlib.sha1(rel.encode('utf-8')).hexdigest()[:10]}"
                    state="MQ5_REVIEW" if f.suffix.lower()==".mq5" else "NEW"
                    if self.store.add(key,str(f),state=state): self.log(f"intake: {z.name} -> {f.name}")

    def tick_demo(self):
        s=self.store
        if s.by_state("NEW"):
            r=s.by_state("NEW")[0]; self.active_card="convert"
            s.move(r.name,"CONVERTED","translated"); self.log(f"convert: {r.name}.mq4 -> {r.name}.mq5")
        elif s.by_state("CONVERTED"):
            r=s.by_state("CONVERTED")[0]; self.active_card="compile"
            if random.random()>0.45:
                s.move(r.name,"DELIVERED","compiled clean",errors=0); self.processed+=1
                self.log(f"compiled clean -> OUTPUT: {r.name}.mq5","good")
            else:
                e=random.randint(1,8); s.move(r.name,"NEEDS_REPAIR",f"{e} errors",errors=e)
                self.log(f"needs review ({e} errors): {r.name}.mq5","warn")
        elif s.by_state("NEEDS_REPAIR"):
            r=s.by_state("NEEDS_REPAIR")[0]; self.active_card="repair"
            if random.random()>0.4:
                s.move(r.name,"DELIVERED","Gen2 repaired",errors=0); self.processed+=1
                self.log(f"REPAIRED -> compiled: {r.name}.mq5","good")
            else:
                s.move(r.name,"AI_QUEUE","queued for AI")
                self.log(f"Gen2 exhausted -> AI queue: {r.name}.mq5","warn")
        elif s.by_state("AI_QUEUE") and self.cfg.agent_enabled:
            r=s.by_state("AI_QUEUE")[0]; self.active_card="aifix"; r.attempts+=1
            if random.random()>0.5:
                s.move(r.name,"DELIVERED","AI fixed",errors=0); self.processed+=1
                self.log(f"AI fixed -> OUTPUT: {r.name}.mq5","good")
            elif r.attempts>=2:
                s.move(r.name,"BLOCKED","AI could not fix")
                self.log(f"AI could not fix (kept): {r.name}.mq5","error")
        self._tick()

    def _tick(self):
        if self.run_gate.is_set() and self.run_had_work:
            pending=sum(len(self.store.by_state(x)) for x in
                        ("NEW","MQ5_REVIEW","CONVERTED","NEEDS_REPAIR","AI_QUEUE"))
            if pending==0:
                counts=self.store.counts(); elapsed=max(0,time.time()-self.run_started_at)
                delivered=max(0,counts["DELIVERED"]-self.run_baseline_counts.get("DELIVERED",0))
                delivered+=max(0,counts["MQ5_VERIFIED"]-self.run_baseline_counts.get("MQ5_VERIFIED",0))
                blocked=max(0,counts["BLOCKED"]-self.run_baseline_counts.get("BLOCKED",0))
                blocked+=max(0,counts["MQ5_REJECTED"]-self.run_baseline_counts.get("MQ5_REJECTED",0))
                self.run_gate.clear(); self.active_card=""; self.agent_state="completed"
                self.agent_last_event=(f"Compiler finished: {delivered} delivered, "
                                       f"{blocked} blocked in {elapsed:.1f}s")
                self.log(f"COMPILER RUN #{self.run_number} FINISHED — "
                         f"{delivered} delivered, {blocked} blocked — "
                         f"elapsed {elapsed:.1f}s","good")
                self.emit("run_finished",run_number=self.run_number,elapsed=elapsed,
                          delivered=delivered,blocked=blocked)
        ks=self.knowledge.stats() if self.knowledge else {"total":0}
        self.emit("tick",active=self.active_card,processed=self.processed,rate=self.rate(),
                  next_agent_run=self._next_agent_run,agent_started=self.agent_started,
                  agent_file=self.agent_file,agent_state=self.agent_state,
                  agent_last_event=self.agent_last_event,agent_completed=self.agent_completed,
                  knowledge_total=ks.get("total",0),
                  agent_avg=(sum(self.agent_durations)/len(self.agent_durations)
                             if self.agent_durations else 60.0),
                  agent_interval=max(0,int(self.cfg.agent_cooldown_seconds)),
                  agent_workers=max(1,int(self.cfg.agent_workers)),
                  run_active=self.run_gate.is_set())
    def rate(self): return self.processed/max(1e-6,time.time()-self.t0)*60.0

class App(_DND_BASE):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME}  v{APP_VERSION}"); self.geometry("1060x700"); self.configure(bg=BG)
        self.cfg=Config.load()
        self.work=BASE/"_work"; self.work.mkdir(exist_ok=True)
        self.ledger=self.work/"ledger.csv"; self.events_log=self.work/"events.log"
        self.diag_dir=Path(self.cfg.output_folder)/"_diagnostics"
        self.store=Store(ledger_path=self.ledger)
        self.bus=queue.Queue(); self.engine=None; self._events=[]
        self._style(); self._header()
        self.nb=ttk.Notebook(self); self.nb.pack(fill="both",expand=True,padx=10,pady=(0,10))
        self.tab_dash=self._build_dashboard(); self.tab_files=self._build_files()
        self.tab_mq5=self._build_mq5_validation()
        self.tab_codex=self._build_codex_live()
        self.tab_memory=self._build_memory()
        self.tab_blocked=self._build_blocked(); self.tab_config=self._build_settings()
        self.tab_update=self._build_updates(); self.tab_log=self._build_log()
        self.start_engine(); self.after(300,self._drain)

    def _style(self):
        st=ttk.Style(self)
        try: st.theme_use("clam")
        except Exception: pass
        st.configure("TNotebook",background=BG,borderwidth=0)
        st.configure("TNotebook.Tab",padding=(16,8),font=("Segoe UI",10),
                     background=CARD,foreground=MUTE,borderwidth=0)
        st.map("TNotebook.Tab",background=[("selected",BG)],foreground=[("selected",BLUE)])
        st.configure("TFrame",background=CARD)
        st.configure("Treeview",rowheight=24,font=("Segoe UI",9),background=CARD,
                     fieldbackground=CARD,foreground=INK,borderwidth=0)
        st.map("Treeview",background=[("selected","#123437")],foreground=[("selected",BLUE)])
        st.configure("Treeview.Heading",font=("Segoe UI",9,"bold"),background=PANEL2,
                     foreground=MUTE,borderwidth=0)
        st.configure("TCombobox",fieldbackground=PANEL2,background=PANEL2,foreground=INK)

    def _header(self):
        h=tk.Frame(self,bg=BG); h.pack(fill="x",padx=12,pady=10)
        tk.Label(h,text=APP_NAME,bg=BG,fg=INK,font=("Segoe UI",16,"bold")).pack(side="left")
        self.mode_lbl=tk.Label(h,text=f"  v{APP_VERSION} · single file · modular",
                 bg=BG,fg=MUTE,font=("Segoe UI",9)); self.mode_lbl.pack(side="left")
        self.status_lbl=tk.Label(h,text="● Ready",bg=BG,fg=MUTE,font=("Segoe UI",10,"bold"))
        self.status_lbl.pack(side="right")
        self.pause_btn=tk.Button(h,text="Pause",width=8,command=self.toggle_pause,state="disabled",
                 bg=PANEL2,fg=INK,activebackground=CARD,activeforeground=BLUE,relief="flat",bd=0)
        self.pause_btn.pack(side="right",padx=6)
        self.start_btn=tk.Button(h,text="▶ START COMPILER",command=self.start_processing,
                 bg=GREEN,fg=BLACK,activebackground=CYANDP,activeforeground=BLACK,
                 relief="flat",bd=0,font=("Segoe UI",9,"bold"),padx=12)
        self.start_btn.pack(side="right",padx=6)
        tk.Button(h,text="⬇ Export Diagnostics",command=self.export_diag,
                 bg=BLUE,fg=BLACK,activebackground=CYANDP,activeforeground=BLACK,
                 relief="flat",bd=0,font=("Segoe UI",9,"bold"),padx=10).pack(side="right",padx=6)

    def _build_dashboard(self):
        f=tk.Frame(self.nb,bg=CARD); self.nb.add(f,text="  Dashboard  ")
        self.activity=tk.Label(f,text="idle",bg=PANEL2,fg=BLUE,font=("Consolas",11),
                               anchor="w",padx=14); self.activity.pack(fill="x",padx=14,pady=(14,8),ipady=8)
        drop=tk.Frame(f,bg=PANEL2,highlightbackground=BLUE,highlightthickness=2,height=54)
        drop.pack(fill="x",padx=14,pady=(0,8)); drop.pack_propagate(False)
        hint=("\u2b07  Drop .mq4 / .zip here  \u2014  or click to add files"
              +("" if _HAS_DND else "   (drag needs tkinterdnd2; click always works)"))
        lbl=tk.Label(drop,text=hint,bg=PANEL2,fg=INK,font=("Segoe UI",11,"bold")); lbl.pack(expand=True)
        for w in (drop,lbl): w.bind("<Button-1>",lambda e:self.add_files_dialog())
        if _HAS_DND:
            try: drop.drop_target_register(DND_FILES); drop.dnd_bind("<<Drop>>",self._on_drop)
            except Exception: pass
        cards=tk.Frame(f,bg=CARD); cards.pack(fill="x",padx=10); self.card_widgets={}
        specs=[("1 Watching","intake","NEW",MUTE),("2 Convert","convert","CONVERTED",INK),
               ("3 Compile","compile","COMPILED",INK),("4 Auto-fix","repair","NEEDS_REPAIR",INK),
               ("5 AI fix","aifix","AI_QUEUE",BLUE),("6 MQ5 test","mq5test","MQ5_REVIEW",AMBER),
               ("7 Output","output","DELIVERED",GREEN)]
        for i,(title,card,state,color) in enumerate(specs):
            c=tk.Frame(cards,bg=CARD,highlightbackground=LINE2,highlightthickness=1,cursor="hand2")
            c.grid(row=0,column=i,sticky="nsew",padx=4,pady=4); cards.grid_columnconfigure(i,weight=1)
            tk.Label(c,text=title,bg=CARD,fg=MUTE,font=("Segoe UI",9)).pack(pady=(10,0))
            n=tk.Label(c,text="0",bg=CARD,fg=color,font=("Segoe UI",22,"bold")); n.pack()
            tk.Label(c,text=state,bg=CARD,fg=DIM,font=("Consolas",8)).pack(pady=(0,10))
            for w in (c,n): w.bind("<Button-1>",lambda e,s=state:self.jump_to_files(s))
            self.card_widgets[card]=(c,n)
        s=tk.Frame(f,bg=CARD); s.pack(fill="x",padx=14,pady=12)
        self.stat_rate=self._stat(s,"Throughput","0.0 /min",0); self.stat_eta=self._stat(s,"Backlog ETA","--",1)
        self.stat_done=self._stat(s,"Delivered","0",2); self.stat_blk=self._stat(s,"Blocked","0",3)
        tk.Label(f,text="Recent activity",bg=CARD,fg=INK,font=("Segoe UI",10,"bold"),
                 anchor="w").pack(fill="x",padx=16,pady=(6,2))
        self.feed=scrolledtext.ScrolledText(f,height=8,bg=DARK,fg=DARKINK,font=("Consolas",9),
                 relief="flat",bd=0,insertbackground=INK); self.feed.pack(fill="both",expand=True,padx=14,pady=(0,12))
        for t,c in [("good",GREEN),("warn",AMBER),("error",RED)]: self.feed.tag_config(t,foreground=c)
        return f
    def _stat(self,parent,label,val,col):
        b=tk.Frame(parent,bg=CARD,highlightbackground=LINE2,highlightthickness=1)
        b.grid(row=0,column=col,sticky="nsew",padx=4); parent.grid_columnconfigure(col,weight=1)
        tk.Label(b,text=label,bg=CARD,fg=MUTE,font=("Segoe UI",8)).pack(pady=(8,0))
        v=tk.Label(b,text=val,bg=CARD,fg=INK,font=("Segoe UI",15,"bold")); v.pack(pady=(0,8)); return v

    def _build_codex_live(self):
        f=tk.Frame(self.nb,bg=CARD); self.nb.add(f,text="  Codex Live  ")
        top=tk.Frame(f,bg=CARD); top.pack(fill="x",padx=14,pady=14)
        self.cx_status=self._stat(top,"Codex status","Idle",0)
        self.cx_elapsed=self._stat(top,"Current elapsed","00:00",1)
        self.cx_next=self._stat(top,"Next agent run","Ready",2)
        self.cx_queue=self._stat(top,"Queue remaining","0",3)
        row=tk.Frame(f,bg=PANEL2,highlightbackground=LINE2,highlightthickness=1)
        row.pack(fill="x",padx=14,pady=(0,10))
        self.cx_file=tk.Label(row,text="Current file: none",bg=PANEL2,fg=BLUE,
             font=("Segoe UI",10,"bold"),anchor="w",padx=12); self.cx_file.pack(fill="x",pady=(9,2))
        self.cx_action=tk.Label(row,text="Waiting for queued work",bg=PANEL2,fg=INK,
             font=("Consolas",9),anchor="w",justify="left",wraplength=980,padx=12)
        self.cx_action.pack(fill="x",pady=(0,9))
        kb=tk.Frame(f,bg=CARD); kb.pack(fill="x",padx=14,pady=(0,8))
        self.cx_memory=tk.Label(kb,text="Repair knowledge: 0 verified fixes",bg=CARD,fg=GREEN,
             font=("Segoe UI",10,"bold"),anchor="w"); self.cx_memory.pack(side="left")
        tk.Label(kb,text="Stored in _work\\repair_knowledge.sqlite3",bg=CARD,fg=MUTE,
             font=("Consolas",8)).pack(side="right")
        tk.Label(f,text="Live Codex event stream",bg=CARD,fg=INK,font=("Segoe UI",10,"bold"),
                 anchor="w").pack(fill="x",padx=16,pady=(4,2))
        self.cx_log=scrolledtext.ScrolledText(f,bg=DARK,fg=DARKINK,font=("Consolas",9),
                 relief="flat",bd=0,insertbackground=INK)
        self.cx_log.pack(fill="both",expand=True,padx=14,pady=(0,12))
        for t,c in [("event",DARKINK),("heartbeat",MUTE),("verify",BLUE),("stderr",AMBER),
                    ("error",RED)]: self.cx_log.tag_config(t,foreground=c)
        return f

    def _build_files(self):
        f=tk.Frame(self.nb,bg=CARD); self.nb.add(f,text="  Files  ")
        bar=tk.Frame(f,bg=CARD); bar.pack(fill="x",padx=12,pady=8)
        tk.Label(bar,text="Filter:",bg=CARD,fg=MUTE).pack(side="left")
        self.filter_var=tk.StringVar(value="ALL")
        om=ttk.Combobox(bar,textvariable=self.filter_var,width=16,values=["ALL"]+STATES,state="readonly")
        om.pack(side="left",padx=6); om.bind("<<ComboboxSelected>>",lambda e:self.refresh_files())
        cols=("name","state","progress","errors","attempts","detail")
        holder=tk.Frame(f,bg=CARD); holder.pack(fill="both",expand=True,padx=12,pady=(0,12))
        self.tree=ttk.Treeview(holder,columns=cols,show="headings")
        for c,w in zip(cols,(190,105,130,50,55,320)):
            self.tree.heading(c,text=c.title()); self.tree.column(c,width=w,anchor="w")
        self._attach_tree_scrollbars(holder,self.tree)
        self.tree.bind("<Double-1>",self.show_detail); return f

    def _attach_tree_scrollbars(self,parent,tree):
        ys=ttk.Scrollbar(parent,orient="vertical",command=tree.yview)
        xs=ttk.Scrollbar(parent,orient="horizontal",command=tree.xview)
        tree.configure(yscrollcommand=ys.set,xscrollcommand=xs.set)
        tree.grid(row=0,column=0,sticky="nsew"); ys.grid(row=0,column=1,sticky="ns")
        xs.grid(row=1,column=0,sticky="ew")
        parent.grid_rowconfigure(0,weight=1); parent.grid_columnconfigure(0,weight=1)

    def _build_mq5_validation(self):
        f=tk.Frame(self.nb,bg=CARD); self.nb.add(f,text="  MQ5 Validation  ")
        tk.Label(f,text="Untrusted MQ5 validation and quarantine",bg=CARD,fg=INK,
                 font=("Segoe UI",12,"bold")).pack(anchor="w",padx=14,pady=(14,2))
        tk.Label(f,text="Drop or browse for existing .mq5 files. MQL ONE performs static capability "
                 "analysis and isolated MetaEditor compilation. Runtime remains NOT TESTED until "
                 "an MT5 Strategy Tester harness verifies handles and buffers.",bg=CARD,fg=MUTE,
                 wraplength=980,justify="left").pack(anchor="w",padx=14,pady=(0,8))
        tk.Button(f,text="＋ Add MQ5 files",command=self.add_files_dialog,bg=BLUE,fg=BLACK,
                  relief="flat",font=("Segoe UI",9,"bold"),padx=12,pady=5).pack(anchor="w",padx=14,pady=(0,8))
        cols=("name","state","compile","risk","quality","runtime","verdict")
        holder=tk.Frame(f,bg=CARD); holder.pack(fill="both",expand=True,padx=12,pady=(0,12))
        self.mq5tree=ttk.Treeview(holder,columns=cols,show="headings")
        for c,w in zip(cols,(220,120,80,75,65,115,340)):
            self.mq5tree.heading(c,text=c.title()); self.mq5tree.column(c,width=w,anchor="w")
        self._attach_tree_scrollbars(holder,self.mq5tree)
        self.mq5tree.bind("<Double-1>",self._show_mq5_report)
        return f

    def refresh_mq5(self):
        self.mq5tree.delete(*self.mq5tree.get_children())
        for r in self.store.all():
            if r.state not in ("MQ5_REVIEW","MQ5_VERIFIED","MQ5_REJECTED"): continue
            v=r.validation or {}
            if not v and "report=" in r.detail:
                try:
                    rp=Path(r.detail.split("report=",1)[1]); v=json.loads(rp.read_text("utf-8")); r.validation=v
                except Exception: pass
            cp=v.get("compile") or {}; rt=v.get("runtime") or {}
            self.mq5tree.insert("","end",iid=r.name,values=(r.name,r.state,
                cp.get("status","QUEUED"),v.get("risk","--"),v.get("quality_score","--"),
                rt.get("status","NOT TESTED"),r.detail))

    def _show_mq5_report(self,_e):
        r=self.store.records.get(self.mq5tree.focus())
        if not r:return
        w=tk.Toplevel(self); w.title("MQ5 validation: "+r.name); w.geometry("900x650"); w.configure(bg=CARD)
        t=scrolledtext.ScrolledText(w,bg=DARK,fg=DARKINK,font=("Consolas",9),relief="flat")
        t.pack(fill="both",expand=True,padx=12,pady=12)
        t.insert("end",json.dumps(r.validation or {"status":r.state,"detail":r.detail},indent=2,default=str))

    def _build_memory(self):
        f=tk.Frame(self.nb,bg=CARD); self.nb.add(f,text="  Repair Memory  ")
        tk.Label(f,text="Compile-verified repair repository",bg=CARD,fg=INK,
                 font=("Segoe UI",12,"bold")).pack(anchor="w",padx=14,pady=(14,2))
        tk.Label(f,text="Every accepted Gen2 or Codex repair records its compiler error signature, "
                 "summary, and source diff. Matching history is supplied to future Codex jobs.",
                 bg=CARD,fg=MUTE,wraplength=980,justify="left").pack(anchor="w",padx=14,pady=(0,10))
        cols=("time","name","method","errors","summary")
        holder=tk.Frame(f,bg=CARD); holder.pack(fill="both",expand=True,padx=12,pady=(0,12))
        self.mem_tree=ttk.Treeview(holder,columns=cols,show="headings")
        for c,w in zip(cols,(130,230,125,70,430)):
            self.mem_tree.heading(c,text=c.title()); self.mem_tree.column(c,width=w,anchor="w")
        self._attach_tree_scrollbars(holder,self.mem_tree)
        self._memory_rows={}; self._next_memory_refresh=0
        self.mem_tree.bind("<Double-1>",self._show_memory)
        return f

    def _refresh_memory(self):
        if time.time()<self._next_memory_refresh:return
        self._next_memory_refresh=time.time()+5
        kb=getattr(self.engine,"knowledge",None) if self.engine else None
        if not kb:return
        try: rows=kb.recent(200)
        except Exception:return
        self.mem_tree.delete(*self.mem_tree.get_children()); self._memory_rows={}
        for r in rows:
            iid=str(r["id"]); self._memory_rows[iid]=r
            summary=(r.get("changes") or "").replace("\n"," ")[:500]
            self.mem_tree.insert("","end",iid=iid,values=(r["created_at"],r["source_name"],
                r["method"],f"{r['errors_before']}→{r['errors_after']}",summary))

    def _show_memory(self,_e):
        r=self._memory_rows.get(self.mem_tree.focus())
        if not r:return
        w=tk.Toplevel(self); w.title("Repair memory: "+r["source_name"])
        w.geometry("850x650"); w.configure(bg=CARD)
        tk.Label(w,text=f"{r['source_name']}  ·  {r['method']}  ·  "
                 f"errors {r['errors_before']}→{r['errors_after']}",bg=CARD,fg=INK,
                 font=("Segoe UI",11,"bold")).pack(anchor="w",padx=14,pady=10)
        t=scrolledtext.ScrolledText(w,bg=DARK,fg=DARKINK,font=("Consolas",9),relief="flat")
        t.pack(fill="both",expand=True,padx=12,pady=(0,12))
        t.insert("end","ERROR SIGNATURE\n"+(r.get("error_signature") or "")+
                 "\n\nREPAIR SUMMARY\n"+(r.get("changes") or "")+
                 "\n\nCOMPILE-VERIFIED PATCH\n"+(r.get("patch") or ""))
    def jump_to_files(self,state):
        self.filter_var.set(state); self.nb.select(self.tab_files); self.refresh_files()
    def refresh_files(self):
        self.tree.delete(*self.tree.get_children()); want=self.filter_var.get()
        for r in self.store.all():
            if want!="ALL" and r.state!=want: continue
            self.tree.insert("","end",iid=r.name,values=(r.name,r.state,
                progress_bar(STAGE_PCT.get(r.state,0)),r.errors,r.attempts,r.detail))
    def show_detail(self,_e,tree=None):
        tree=tree or self.tree; sel=tree.focus()
        if not sel: return
        r=self.store.records.get(sel)
        if not r: return
        w=tk.Toplevel(self); w.title(r.name); w.geometry("560x400"); w.configure(bg=CARD)
        tk.Label(w,text=r.name,bg=CARD,fg=INK,font=("Segoe UI",13,"bold")).pack(anchor="w",padx=16,pady=10)
        tk.Label(w,text=f"State: {r.state}   Errors: {r.errors}   Attempts: {r.attempts}",
                 bg=CARD,fg=MUTE).pack(anchor="w",padx=16)
        tk.Label(w,text=f"Detail: {r.detail}",bg=CARD,fg=AMBER,wraplength=520,
                 justify="left").pack(anchor="w",padx=16,pady=(4,0))
        tk.Label(w,text=f"Path: {r.path}",bg=CARD,fg=DIM,font=("Consolas",8)).pack(anchor="w",padx=16)
        tk.Label(w,text="History:",bg=CARD,fg=INK,font=("Segoe UI",10,"bold")).pack(anchor="w",padx=16,pady=(12,2))
        t=scrolledtext.ScrolledText(w,bg=DARK,fg=DARKINK,font=("Consolas",9),relief="flat")
        t.pack(fill="both",expand=True,padx=14,pady=(0,12))
        for ts,a,b,d in r.history: t.insert("end",f"{ts}  {a:>12} -> {b:<12}  {d}\n")

    def add_files_dialog(self):
        p=filedialog.askopenfilenames(filetypes=[("MQL source / archives","*.mq4 *.mq5 *.zip"),("All files","*.*")])
        if p: self.add_files(p)
    def _on_drop(self,event):
        try: paths=self.tk.splitlist(event.data)
        except Exception: paths=[event.data]
        self.add_files(paths)
    def add_files(self,paths):
        n=0
        for p in paths:
            pp=Path(str(p))
            if pp.suffix.lower() in (".mq4",".mq5") or not pp.suffix:
                state="MQ5_REVIEW" if pp.suffix.lower()==".mq5" else "NEW"
                key=pp.stem
                old=self.store.records.get(key)
                if old and Path(old.path)!=pp:
                    key=f"{pp.stem}__{hashlib.sha1(str(pp).encode('utf-8')).hexdigest()[:10]}"
                if self.store.add(key,str(pp),state=state): n+=1
        if n: self.log_line(f"intake: added {n} file(s) via drop/browse","good")

    def _build_blocked(self):
        f=tk.Frame(self.nb,bg=CARD); self.nb.add(f,text="  Blocked  ")
        tk.Label(f,text="Blocked \u2014 missing include / manual migration",bg=CARD,fg=INK,
                 font=("Segoe UI",11,"bold")).pack(anchor="w",padx=14,pady=(12,2))
        tk.Label(f,text="Files needing a .mqh that isn't on the machine, or the iCustom handle "
                 "migration. Drop the missing library in the watch folder to auto-retry.",
                 bg=CARD,fg=MUTE,justify="left").pack(anchor="w",padx=14)
        cols=("name","attempts","detail"); holder=tk.Frame(f,bg=CARD)
        holder.pack(fill="both",expand=True,padx=12,pady=12)
        self.btree=ttk.Treeview(holder,columns=cols,show="headings")
        for c,w in zip(cols,(240,80,470)):
            self.btree.heading(c,text=c.title()); self.btree.column(c,width=w,anchor="w")
        self._attach_tree_scrollbars(holder,self.btree)
        self.btree.bind("<Double-1>",lambda e:self.show_detail(e,self.btree)); return f
    def refresh_blocked(self):
        self.btree.delete(*self.btree.get_children())
        for r in self.store.by_state("BLOCKED"):
            self.btree.insert("","end",iid=r.name,values=(r.name,r.attempts,r.detail))

    def _build_settings(self):
        outer=tk.Frame(self.nb,bg=CARD); self.nb.add(outer,text="  Settings  "); self.cfg_vars={}
        canvas=tk.Canvas(outer,bg=CARD,highlightthickness=0); vs=ttk.Scrollbar(outer,orient="vertical",command=canvas.yview)
        canvas.configure(yscrollcommand=vs.set); canvas.pack(side="left",fill="both",expand=True); vs.pack(side="right",fill="y")
        f=tk.Frame(canvas,bg=CARD); win=canvas.create_window((0,0),window=f,anchor="nw")
        f.bind("<Configure>",lambda e:canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",lambda e:canvas.itemconfigure(win,width=e.width))
        wheel=lambda e:canvas.yview_scroll(int(-e.delta/120),"units")
        canvas.bind("<Enter>",lambda e:canvas.bind_all("<MouseWheel>",wheel))
        canvas.bind("<Leave>",lambda e:canvas.unbind_all("<MouseWheel>"))
        rows=[("watch_folder","Watch folder (read from)","dir"),
              ("output_folder","Output folder (deliver to)","dir"),
              ("metaeditor_path","MetaEditor (MUST be MT5 metaeditor64.exe)","file"),
              ("codex_path","Codex CLI (codex.exe or codex.cmd)","file")]
        for i,(key,label,kind) in enumerate(rows):
            tk.Label(f,text=label,bg=CARD,fg=INK,anchor="w",font=("Segoe UI",10)).grid(
                row=i,column=0,sticky="w",padx=(18,8),pady=10)
            var=tk.StringVar(value=getattr(self.cfg,key)); self.cfg_vars[key]=var
            e=tk.Entry(f,textvariable=var,width=58,bg=PANEL2,fg=INK,insertbackground=INK,
                     relief="flat",highlightbackground=LINE2,highlightthickness=1)
            e.grid(row=i,column=1,sticky="we",pady=10)
            tk.Button(f,text="Browse…",bg=PANEL2,fg=INK,relief="flat",activebackground=CARD,
                      activeforeground=BLUE,command=lambda k=key,kd=kind:self.browse(k,kd)).grid(row=i,column=2,padx=8)
            if key=="metaeditor_path":
                var.trace_add("write", lambda *a: self._check_meta_path())
        # compiler warning line
        self.meta_warn=tk.Label(f,text="",bg=CARD,fg=RED,anchor="w",wraplength=740,justify="left")
        self.meta_warn.grid(row=len(rows),column=0,columnspan=3,sticky="w",padx=18,pady=(0,4))
        opt=tk.Frame(f,bg=CARD); opt.grid(row=len(rows)+1,column=0,columnspan=3,sticky="w",padx=18,pady=8)
        self.cfg_vars["repair_passes"]=tk.IntVar(value=self.cfg.repair_passes)
        self.cfg_vars["agent_cooldown_seconds"]=tk.IntVar(value=self.cfg.agent_cooldown_seconds)
        self.cfg_vars["agent_timeout_seconds"]=tk.IntVar(value=self.cfg.agent_timeout_seconds)
        self.cfg_vars["compile_workers"]=tk.IntVar(value=self.cfg.compile_workers)
        self.cfg_vars["agent_workers"]=tk.IntVar(value=self.cfg.agent_workers)
        for j,(k,lab) in enumerate([("repair_passes","Repair passes"),
                                    ("agent_cooldown_seconds","AI cooldown sec"),
                                    ("agent_timeout_seconds","AI timeout sec"),
                                    ("compile_workers","Compile workers"),
                                    ("agent_workers","Codex workers")]):
            row=j//3; col=(j%3)*2
            tk.Label(opt,text=lab,bg=CARD,fg=MUTE).grid(row=row,column=col,padx=(0,6),pady=4)
            low=0 if k=="agent_cooldown_seconds" else 1
            high=3600 if "seconds" in k else (8 if "workers" in k else 99)
            tk.Spinbox(opt,from_=low,to=high,width=5,bg=PANEL2,fg=INK,relief="flat",
                       buttonbackground=PANEL2,textvariable=self.cfg_vars[k]).grid(row=row,column=col+1,padx=(0,18),pady=4)
        fast=tk.Frame(f,bg=CARD); fast.grid(row=len(rows)+2,column=0,columnspan=3,sticky="w",padx=18,pady=8)
        self.cfg_vars["codex_model"]=tk.StringVar(value=self.cfg.codex_model)
        self.cfg_vars["codex_reasoning_effort"]=tk.StringVar(value=self.cfg.codex_reasoning_effort)
        tk.Label(fast,text="Codex repair model",bg=CARD,fg=MUTE).grid(row=0,column=0,padx=(0,6))
        ttk.Combobox(fast,textvariable=self.cfg_vars["codex_model"],width=20,state="readonly",
                     values=("gpt-5.6-luna","gpt-5.6-terra","gpt-5.6-sol","")).grid(row=0,column=1,padx=(0,18))
        tk.Label(fast,text="Reasoning",bg=CARD,fg=MUTE).grid(row=0,column=2,padx=(0,6))
        ttk.Combobox(fast,textvariable=self.cfg_vars["codex_reasoning_effort"],width=10,state="readonly",
                     values=("low","medium","high","")).grid(row=0,column=3,padx=(0,18))
        tk.Label(fast,text="Luna + low is the fastest compile-gated profile. Use Terra/medium for hard repairs.",
                 bg=CARD,fg=AMBER).grid(row=1,column=0,columnspan=4,sticky="w",pady=(6,0))
        tk.Button(f,text="Save settings",command=self.save_settings,bg=BLUE,fg=BLACK,
                  font=("Segoe UI",10,"bold"),relief="flat",padx=16,pady=6,
                  activebackground=CYANDP,activeforeground=BLACK).grid(row=len(rows)+3,column=1,sticky="w",pady=16)
        f.grid_columnconfigure(1,weight=1)
        self.after(500, self._check_meta_path)
        return outer
    def _check_meta_path(self):
        try:
            path=self.cfg_vars["metaeditor_path"].get()
            comp=MODULES.get("compile")
            if path and comp and comp.is_probably_mt4(path):
                self.meta_warn.config(text="⚠ This looks like MetaTrader 4's metaeditor.exe — it "
                    "CANNOT compile .mq5 files (you'll get 'NO LOG WRITTEN'). Install MetaTrader 5 "
                    "and choose its metaeditor64.exe instead.")
            elif path and comp and path.lower().endswith("metaeditor64.exe"):
                self.meta_warn.config(text="✓ MetaTrader 5 editor selected.", fg=GREEN)
            else:
                self.meta_warn.config(text="", fg=RED)
        except Exception: pass
    def browse(self,key,kind):
        p=filedialog.askdirectory() if kind=="dir" else filedialog.askopenfilename()
        if p: self.cfg_vars[key].set(p)
    def save_settings(self):
        for k,var in self.cfg_vars.items(): setattr(self.cfg,k,var.get())
        self.cfg.save(); self.diag_dir=Path(self.cfg.output_folder)/"_diagnostics"
        messagebox.showinfo(APP_NAME,"Settings saved to config.json\n\nRestart the app so the "
                            "engine picks up the new MetaEditor path.")
        self.log_line(f"settings saved: watch={self.cfg.watch_folder}","good")

    def _build_updates(self):
        f=tk.Frame(self.nb,bg=CARD); self.nb.add(f,text="  Updates  ")
        tk.Label(f,text="Modular components",bg=CARD,fg=INK,font=("Segoe UI",12,"bold")).pack(
            anchor="w",padx=18,pady=(16,4))
        tk.Label(f,text="Each pipeline stage is a swappable module. Patch by pointing at a source "
                 "folder and clicking below — no rebuild.",bg=CARD,fg=MUTE,justify="left").pack(anchor="w",padx=18)
        self.mod_box=tk.Frame(f,bg=CARD,highlightbackground=LINE2,highlightthickness=1)
        self.mod_box.pack(fill="x",padx=18,pady=12); self._render_modules()
        tk.Label(f,text="Update source (folder / network share you control):",bg=CARD,fg=INK).pack(anchor="w",padx=18,pady=(8,2))
        row=tk.Frame(f,bg=CARD); row.pack(fill="x",padx=18); self.update_src=tk.StringVar()
        tk.Entry(row,textvariable=self.update_src,width=54,bg=PANEL2,fg=INK,insertbackground=INK,
                 relief="flat",highlightbackground=LINE2,highlightthickness=1).pack(side="left")
        tk.Button(row,text="Browse…",bg=PANEL2,fg=INK,relief="flat",activebackground=CARD,activeforeground=BLUE,
                  command=lambda:self.update_src.set(filedialog.askdirectory() or self.update_src.get())).pack(side="left",padx=6)
        tk.Button(f,text="Check for updates",command=self.do_update,bg=BLUE,fg=BLACK,relief="flat",
                  padx=16,pady=6,activebackground=CYANDP,activeforeground=BLACK,
                  font=("Segoe UI",10,"bold")).pack(anchor="w",padx=18,pady=12)
        self.update_status=tk.Label(f,text="",bg=CARD,fg=MUTE); self.update_status.pack(anchor="w",padx=18); return f
    def _render_modules(self):
        for w in self.mod_box.winfo_children(): w.destroy()
        for i,(mod,ver) in enumerate(read_manifest().items()):
            tk.Label(self.mod_box,text=f"modules/{mod}.py",bg=CARD,fg=INK,font=("Consolas",10)).grid(
                row=i,column=0,sticky="w",padx=14,pady=6)
            tk.Label(self.mod_box,text=f"v{ver}",bg=CARD,fg=GREEN,font=("Consolas",10)).grid(row=i,column=1,padx=20)
            loaded=MODULES.get(mod) is not None
            tk.Label(self.mod_box,text="loaded" if loaded else "bundled default",bg=CARD,
                     fg=MUTE if loaded else DIM).grid(row=i,column=2,sticky="w")
        self.mod_box.grid_columnconfigure(2,weight=1)
    def do_update(self):
        msg=check_for_updates(self.update_src.get()); self.update_status.config(text=msg)
        self._render_modules(); self.log_line(f"update check: {msg}")

    def _build_log(self):
        f=tk.Frame(self.nb,bg=CARD); self.nb.add(f,text="  Log  ")
        self.log=scrolledtext.ScrolledText(f,bg=DARK,fg=DARKINK,font=("Consolas",9),relief="flat",bd=0)
        self.log.pack(fill="both",expand=True,padx=12,pady=12)
        for t,c in [("info",DARKINK),("good",GREEN),("warn",AMBER),("error",RED)]: self.log.tag_config(t,foreground=c)
        return f

    def export_diag(self):
        try:
            if MODULES.get("diagnostics") is None: load_all_modules()
            diag=MODULES.get("diagnostics")
            if diag is None:
                messagebox.showerror(APP_NAME,"diagnostics module not loaded"); return
            zp=diag.build(self.cfg,self.store.all(),self._events,BASE)
            self.log_line(f"diagnostics written: {zp}","good")
            messagebox.showinfo(APP_NAME,f"Diagnostics bundle written to:\n\n{zp}\n\n"
                                "Extract it and send SUMMARY.txt + ledger.csv + a raw_logs file.")
        except Exception as ex:
            messagebox.showerror(APP_NAME,f"diagnostics error: {ex}")
            self.log_line(f"diagnostics error: {ex}","error")

    def start_engine(self):
        self.engine=Engine(self.cfg,self.store,self.bus,events_path=self.events_log,
                           diag_dir=self.diag_dir); self.engine.start()
    def start_processing(self):
        if not self.engine: self.start_engine()
        if self.engine.request_start():
            self.start_btn.config(state="disabled",text="COMPILER RUNNING")
            self.pause_btn.config(state="normal",text="Pause")
            self.status_lbl.config(text="● Compiler started",fg=GREEN)
            self.log_line("COMPILER STARTED — processing queued indicators","good")
    def toggle_pause(self):
        if not self.engine or not self.engine.run_gate.is_set(): return
        if self.engine.paused.is_set():
            self.engine.paused.clear(); self.pause_btn.config(text="Pause")
            self.status_lbl.config(text="● Running",fg=BLUE)
        else:
            self.engine.paused.set(); self.pause_btn.config(text="Resume")
            self.status_lbl.config(text="● Paused",fg=AMBER)

    def log_line(self,msg,level="info"):
        ts=time.strftime("%H:%M:%S"); line=f"{ts}  {msg}"
        self._events.append(line)
        if len(self._events)>5000: self._events=self._events[-4000:]
        self.log.insert("end",line+"\n",level); self.log.see("end")
        self.feed.insert("end",line+"\n",level); self.feed.see("end")

    def _drain(self):
        try:
            while True:
                ev=self.bus.get_nowait()
                if ev["kind"]=="log": self.log_line(ev["msg"],ev.get("level","info"))
                elif ev["kind"]=="tick": self._update_dashboard(ev)
                elif ev["kind"]=="agent_event": self._agent_event(ev)
                elif ev["kind"]=="run_started":
                    self.status_lbl.config(text="● Compiler started",fg=GREEN)
                elif ev["kind"]=="run_finished":
                    self.start_btn.config(state="normal",text="▶ START COMPILER")
                    self.pause_btn.config(state="disabled",text="Pause")
                    self.status_lbl.config(text="● Compiler finished",fg=GREEN)
                    mins=ev.get("elapsed",0)/60
                    messagebox.showinfo(APP_NAME,
                        f"Compiler finished.\n\nDelivered: {ev.get('delivered',0)}\n"
                        f"Blocked: {ev.get('blocked',0)}\nElapsed: {mins:.1f} minutes")
        except queue.Empty: pass
        self.refresh_files(); self.refresh_mq5(); self.refresh_blocked()
        self._refresh_memory()
        if self.engine and not getattr(self,"_mode_set",False):
            self._mode_set=True
            self.mode_lbl.config(text=f"  v{APP_VERSION} · "
                                 f"{'REAL pipeline' if not DEMO_MODE else 'DEMO mode'}")
        self.after(400,self._drain)

    def _update_dashboard(self,ev):
        c=self.store.counts()
        cc={"intake":c["NEW"],"convert":c["CONVERTED"],"compile":c["COMPILED"],
            "repair":c["NEEDS_REPAIR"],"aifix":c["AI_QUEUE"],"mq5test":c["MQ5_REVIEW"],
            "output":c["DELIVERED"]+c["MQ5_VERIFIED"]}
        for card,(frame,lbl) in self.card_widgets.items():
            lbl.config(text=str(cc.get(card,0)))
            frame.config(highlightbackground=BLUE if ev["active"]==card else LINE2,
                         highlightthickness=2 if ev["active"]==card else 1)
        rate=ev["rate"]; remaining=c["NEW"]+c["MQ5_REVIEW"]+c["CONVERTED"]+c["NEEDS_REPAIR"]+c["AI_QUEUE"]
        if c["AI_QUEUE"]:
            wait=max(0,(ev.get("next_agent_run") or 0)-time.time())
            workers=max(1,int(ev.get("agent_workers",1))); waves=(c["AI_QUEUE"]+workers-1)//workers
            secs=wait+waves*ev.get("agent_avg",60)+(max(0,waves-1)*ev.get("agent_interval",0))
            eta=f"{secs/60:.1f} min"
        else: eta=f"{remaining/rate:.1f} min" if rate>0.01 else ("0 min" if remaining==0 else "--")
        self.stat_rate.config(text=f"{rate:.1f} /min"); self.stat_eta.config(text=eta)
        self.stat_done.config(text=str(c["DELIVERED"]+c["MQ5_VERIFIED"]))
        self.stat_blk.config(text=str(c["BLOCKED"]+c["MQ5_REJECTED"]))
        self.activity.config(text=f"stage: {ev['active'] or 'idle'}   "
                             f"delivered {c['DELIVERED']}   blocked {c['BLOCKED']}")
        now=time.time(); started=ev.get("agent_started") or 0
        elapsed=max(0,int(now-started)) if started else 0
        next_at=ev.get("next_agent_run") or 0
        wait=max(0,int(next_at-now)) if c["AI_QUEUE"] else 0
        self.cx_status.config(text=(ev.get("agent_state") or "idle").title())
        self.cx_elapsed.config(text=f"{elapsed//60:02d}:{elapsed%60:02d}")
        self.cx_next.config(text=(f"{wait//60:02d}:{wait%60:02d}" if wait else "Ready"))
        self.cx_queue.config(text=str(c["AI_QUEUE"]))
        self.cx_file.config(text="Current file: "+(ev.get("agent_file") or "none"))
        self.cx_action.config(text=ev.get("agent_last_event") or "Waiting for queued work")
        self.cx_memory.config(text=f"Repair knowledge: {ev.get('knowledge_total',0)} verified fixes")

    def _agent_event(self,ev):
        ts=time.strftime("%H:%M:%S"); kind=ev.get("event_kind","event")
        line=f"{ts}  [{ev.get('file','')}]  {ev.get('message','')}"
        self.cx_log.insert("end",line+"\n",kind if kind in ("event","heartbeat","verify","stderr","error") else "event")
        self.cx_log.see("end")
        self.cx_action.config(text=ev.get("message", ""))
        elapsed=max(0,int(ev.get("elapsed") or 0))
        self.cx_elapsed.config(text=f"{elapsed//60:02d}:{elapsed%60:02d}")
        self.cx_file.config(text="Current file: "+(ev.get("file") or "none"))
        self.cx_status.config(text=(ev.get("event_kind") or "working").title())

if __name__=="__main__":
    MODULES_DIR.mkdir(exist_ok=True); App().mainloop()
