#!/usr/bin/env python3
"""Persistent, compile-gated repair knowledge for MQL ONE."""
from __future__ import annotations
import difflib, hashlib, re, sqlite3, time
from pathlib import Path

MODULE_VERSION="1.1.0"

def error_signature(log):
    rows=[]
    for line in (log or "").splitlines():
        m=re.search(r"error\s+(\d+)\s*:\s*(.*)",line,re.I)
        if not m: continue
        msg=re.sub(r"'[^']+'", "'<id>'", m.group(2).strip())
        msg=re.sub(r"\b\d+\b", "<n>", msg)
        rows.append(f"{m.group(1)}:{msg}")
    return " | ".join(sorted(set(rows)))[:4000]

def _hash(text): return hashlib.sha256((text or "").encode("utf-8","replace")).hexdigest()

class KnowledgeBase:
    def __init__(self,path):
        self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True)
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS repairs(
              id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, source_name TEXT NOT NULL,
              method TEXT NOT NULL, error_signature TEXT NOT NULL,
              errors_before INTEGER, errors_after INTEGER, changes TEXT,
              patch TEXT, before_hash TEXT, after_hash TEXT,
              compile_verified INTEGER NOT NULL DEFAULT 0)""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_repairs_sig ON repairs(error_signature)")
    def _db(self): return sqlite3.connect(str(self.path),timeout=15)
    def record(self,name,method,log,before,after,errors_before,errors_after,changes=""):
        if before==after or int(errors_after or 0)!=0: return False
        patch="".join(difflib.unified_diff(before.splitlines(True),after.splitlines(True),
                    fromfile="before.mq5",tofile="after.mq5",n=3))[:120000]
        sig=error_signature(log)
        with self._db() as db:
            db.execute("""INSERT INTO repairs(created_at,source_name,method,error_signature,
              errors_before,errors_after,changes,patch,before_hash,after_hash,compile_verified)
              VALUES(?,?,?,?,?,?,?,?,?,?,1)""",
              (time.strftime("%Y-%m-%d %H:%M:%S"),name,method,sig,errors_before,
               errors_after,(changes or "")[:12000],patch,_hash(before),_hash(after)))
        return True
    def similar(self,log,limit=5):
        sig=error_signature(log)
        if not sig:return []
        codes=set(re.findall(r"(?:^| \| )(\d+):",sig)); scored=[]
        with self._db() as db:
            db.row_factory=sqlite3.Row
            for row in db.execute("SELECT * FROM repairs WHERE compile_verified=1 ORDER BY id DESC LIMIT 500"):
                rc=set(re.findall(r"(?:^| \| )(\d+):",row["error_signature"] or ""))
                score=len(codes&rc)*10 + (20 if row["error_signature"]==sig else 0)
                if score: scored.append((score,dict(row)))
        return [r for _,r in sorted(scored,key=lambda x:(-x[0],-x[1]["id"]))[:limit]]
    def recent(self,limit=200):
        with self._db() as db:
            db.row_factory=sqlite3.Row
            return [dict(x) for x in db.execute("SELECT * FROM repairs ORDER BY id DESC LIMIT ?",(limit,))]
    def stats(self):
        with self._db() as db:
            total=db.execute("SELECT count(*) FROM repairs WHERE compile_verified=1").fetchone()[0]
            methods=dict(db.execute("SELECT method,count(*) FROM repairs WHERE compile_verified=1 GROUP BY method"))
        return {"total":total,"methods":methods,"path":str(self.path)}

def render_references(rows):
    if not rows:return "No matching compile-verified repair history was found."
    out=["Compile-verified repairs with overlapping MetaEditor error families:"]
    for i,r in enumerate(rows,1):
        out += [f"\nREFERENCE {i}: {r['source_name']} via {r['method']}",
                f"Errors: {r['errors_before']} -> {r['errors_after']}",
                f"Summary: {r.get('changes') or '(no summary)'}",
                "Patch (reference only; adapt semantically, never apply blindly):",
                (r.get("patch") or "")[:18000]]
    return "\n".join(out)
