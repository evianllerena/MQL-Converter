#!/usr/bin/env python3
"""Static trust and structural validation for untrusted MQ5 indicator source.

This stage never executes the source. It classifies risky capabilities and
checks whether the file has the minimum structure expected of an indicator.
MetaEditor compilation and MT5 runtime testing remain separate gates.
"""
from __future__ import annotations
import hashlib, json, re, time
from pathlib import Path

MODULE_VERSION="1.0.0"

RISK_RULES=(
    ("critical","DLL import",r"#import\s+['\"](?![^'\"]*\.ex5)[^'\"]+['\"]"),
    ("critical","process or shell execution",r"\b(?:ShellExecute|WinExec|CreateProcess)\s*\("),
    ("high","network request",r"\b(?:WebRequest|SocketCreate|SocketConnect|SocketSend|SocketTlsHandshake)\s*\("),
    ("high","trading operation in indicator",r"\b(?:OrderSend|OrderSendAsync|CTrade|PositionOpen|Buy|Sell)\b"),
    ("medium","filesystem write/access",r"\b(?:FileOpen|FileWrite|FileDelete|FolderClean|FolderDelete)\s*\("),
    ("medium","terminal global-variable mutation",r"\bGlobalVariable(?:Set|Del|Temp|SetOnCondition)\s*\("),
    ("medium","chart/template mutation",r"\b(?:ChartApplyTemplate|ChartSetSymbolPeriod|ExpertRemove)\s*\("),
)

def _strip_comments(text):
    text=re.sub(r"/\*.*?\*/","",text,flags=re.S)
    return re.sub(r"//[^\r\n]*","",text)

def analyze(text,name="indicator"):
    clean=_strip_comments(text or ""); findings=[]
    for severity,label,pattern in RISK_RULES:
        hits=list(re.finditer(pattern,clean,re.I))
        if hits: findings.append({"severity":severity,"rule":label,"count":len(hits)})
    has_calc=bool(re.search(r"\bOnCalculate\s*\(",clean))
    has_init=bool(re.search(r"\bOnInit\s*\(",clean))
    buffers=len(re.findall(r"\bSetIndexBuffer\s*\(",clean))
    declared=int((re.search(r"#property\s+indicator_buffers\s+(\d+)",clean,re.I) or [None,"0"])[1])
    plots=int((re.search(r"#property\s+indicator_plots\s+(\d+)",clean,re.I) or [None,"0"])[1])
    price_refs=len(re.findall(r"\b(?:open|high|low|close|time|tick_volume|volume|spread)\s*\[",clean,re.I))
    series_calls=len(re.findall(r"\b(?:CopyRates|CopyBuffer|CopyClose|CopyOpen|CopyHigh|CopyLow|iClose|iOpen|iHigh|iLow)\s*\(",clean))
    return_rates=bool(re.search(r"return\s*\(?(?:rates_total|prev_calculated)\)?\s*;",clean))
    constant_writes=len(re.findall(r"\w+(?:Buffer|buffer)\w*\s*\[[^]]+\]\s*=\s*[-+]?\d+(?:\.\d+)?\s*;",clean))
    structural=[]
    if not has_calc: structural.append("missing OnCalculate")
    if buffers==0: structural.append("no SetIndexBuffer calls")
    if declared and buffers<declared: structural.append(f"declares {declared} buffers but maps {buffers}")
    if price_refs+series_calls==0: structural.append("no detected price/series input")
    if has_calc and not return_rates: structural.append("OnCalculate completion return not detected")
    if constant_writes>=3 and price_refs+series_calls==0: structural.append("possible constant-output placeholder")
    rank={"none":0,"low":1,"medium":2,"high":3,"critical":4}; risk="none"
    for f in findings:
        if rank[f["severity"]]>rank[risk]: risk=f["severity"]
    quality=100
    quality-=30 if not has_calc else 0; quality-=20 if buffers==0 else 0
    quality-=20 if price_refs+series_calls==0 else 0; quality-=10 if has_calc and not return_rates else 0
    quality-=min(20,len(structural)*4); quality=max(0,quality)
    verdict="QUARANTINE" if risk in ("critical","high") else ("REVIEW" if structural else "STATIC_PASS")
    return {"name":name,"sha256":hashlib.sha256((text or "").encode("utf-8","replace")).hexdigest(),
            "time":time.strftime("%Y-%m-%d %H:%M:%S"),"risk":risk,"verdict":verdict,
            "quality_score":quality,"findings":findings,"structural_findings":structural,
            "metrics":{"has_oninit":has_init,"has_oncalculate":has_calc,"declared_buffers":declared,
                       "mapped_buffers":buffers,"plots":plots,"price_references":price_refs,
                       "series_calls":series_calls,"constant_buffer_writes":constant_writes},
            "compile":{"status":"NOT_RUN","errors":None},
            "runtime":{"status":"NOT_TESTED","reason":"MT5 Strategy Tester gate required"}}

def save_report(report,path):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,indent=2),encoding="utf-8")
    return path
