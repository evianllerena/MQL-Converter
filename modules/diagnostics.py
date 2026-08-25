#!/usr/bin/env python3
"""modules/diagnostics.py - build a downloadable DIAGNOSTICS_<ts>.zip (v1.1.0)."""
from __future__ import annotations
import csv, io, json, os, platform, sys, time, zipfile
from pathlib import Path
MODULE_VERSION = "1.4.0"


def _ledger_rows(records):
    for r in records:
        yield {"name": r.name, "state": r.state, "errors": r.errors,
               "attempts": r.attempts, "detail": r.detail, "path": r.path,
               "history": " | ".join(f"{ts} {a}->{b} {d}" for ts, a, b, d in r.history)}


def _writable_probe(folder):
    try:
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        p = folder / f".mqlone_write_test_{os.getpid()}.tmp"
        p.write_text("ok", encoding="utf-8")
        p.unlink()
        return {"path": str(folder), "writable": True, "error": ""}
    except Exception as ex:
        return {"path": str(folder), "writable": False, "error": repr(ex)}


def _file_info(path):
    if not path:
        return {"path": "", "exists": False, "error": "not configured"}
    try:
        p = Path(path)
        s = p.stat()
        return {"path": str(p), "exists": True, "size": s.st_size,
                "mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(s.st_mtime))}
    except Exception as ex:
        return {"path": str(path), "exists": False, "error": repr(ex)}


def build(cfg, records, events, base_dir, max_samples=12):
    base_dir = Path(base_dir)
    outdir = Path(getattr(cfg, "output_folder", base_dir)) or base_dir
    try:
        outdir.mkdir(parents=True, exist_ok=True)
    except Exception:
        outdir = base_dir
    diag_dir = outdir / "_diagnostics"
    zpath = outdir / f"DIAGNOSTICS_{time.strftime('%Y%m%d_%H%M%S')}.zip"
    counts = {}
    for r in records:
        counts[r.state] = counts.get(r.state, 0) + 1
    meta = getattr(cfg, "metaeditor_path", "")
    env = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "python": sys.version,
           "platform": sys.platform, "platform_details": platform.platform(),
           "app_base": str(base_dir), "cwd": os.getcwd(),
           "frozen": bool(getattr(sys, "frozen", False)),
           "executable": sys.executable,
           "metaeditor": _file_info(meta),
           "output_probe": _writable_probe(outdir),
           "watch_probe": _writable_probe(Path(getattr(cfg, "watch_folder", base_dir))),
           "config": {k: getattr(cfg, k) for k in dir(cfg)
                      if not k.startswith("_") and not callable(getattr(cfg, k))},
           "state_counts": counts, "total_files": len(records)}
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("environment.json", json.dumps(env, indent=2, default=str))
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=["name", "state", "errors", "attempts",
                           "detail", "path", "history"])
        w.writeheader()
        for row in _ledger_rows(records):
            w.writerow(row)
        z.writestr("ledger.csv", buf.getvalue())
        z.writestr("events.log", "\n".join(events))
        for sub, pattern, limit in [("raw_logs", "*.log", 100),
                                    ("compile_traces", "*.json", 100)]:
            d = diag_dir / sub
            if d.exists():
                for f in sorted(d.glob(pattern), key=lambda p: p.stat().st_mtime,
                                reverse=True)[:limit]:
                    try:
                        z.write(f, f"{sub}/{f.name}")
                    except Exception:
                        pass
        # Include live-agent evidence and the persistent repair repository.
        jobs=outdir/"_agent_jobs"
        if jobs.exists():
            agent_files=[]
            for pattern in ("agent_events.jsonl","agent_message.txt","compiler.log"):
                agent_files.extend(jobs.rglob(pattern))
            for f in sorted(agent_files,key=lambda p:p.stat().st_mtime,reverse=True)[:150]:
                try: z.write(f,"agent_jobs/"+str(f.relative_to(jobs)).replace("\\","/"))
                except Exception: pass
        kb=Path(base_dir)/"_work"/"repair_knowledge.sqlite3"
        if kb.exists():
            try: z.write(kb,"repair_knowledge.sqlite3")
            except Exception: pass
        validation=outdir/"_validation"/"reports"
        if validation.exists():
            for f in sorted(validation.glob("*.json"),key=lambda p:p.stat().st_mtime,reverse=True)[:500]:
                try:z.write(f,"mq5_validation/"+f.name)
                except Exception:pass
        failed = [r for r in records if r.state in ("NEEDS_REPAIR", "AI_QUEUE", "BLOCKED")]
        ok = [r for r in records if r.state == "DELIVERED"]
        for r in failed[:max_samples]:
            p = Path(r.path)
            if p.exists():
                try:
                    z.write(p, f"samples_failed/{p.name}")
                except Exception:
                    pass
        for r in ok[:4]:
            p = Path(r.path)
            if p.exists():
                try:
                    z.write(p, f"samples_ok/{p.name}")
                except Exception:
                    pass
        lines = [f"MQL ONE diagnostics - {env['time']}",
                 f"total files: {len(records)}",
                 "state counts: " + ", ".join(f"{k}={v}" for k, v in counts.items()),
                 f"MetaEditor exists: {env['metaeditor'].get('exists')} :: {meta}",
                 f"Output writable: {env['output_probe']['writable']} :: {outdir}",
                 "", "Top 'detail' reasons among failed files:"]
        reasons = {}
        for r in failed:
            key = (r.detail or "").split(";")[0][:300]
            reasons[key] = reasons.get(key, 0) + 1
        for key, n in sorted(reasons.items(), key=lambda x: -x[1])[:20]:
            lines.append(f"  {n:5d}  {key}")
        z.writestr("SUMMARY.txt", "\n".join(lines))
    return zpath
