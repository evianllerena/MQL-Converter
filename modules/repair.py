#!/usr/bin/env python3
"""modules/repair.py - compile-error-driven repair (Gen2-style).

v2.1.0  MERGE of the proven post_repair adaptive engine into the module,
        keeping the public interface untouched:
            MODULE_VERSION
            apply_rules(name, code, log_text, passno=1) -> (code, [changes])
            looks_dependency_blocked(log_text)          -> str|None
            repair(name, code, log_text, compile_fn, meta, mq5_path, passes=3)

        New fixers are LOG-DRIVEN: they only fire when the compiler actually
        reported that error family, so a passing file is never mutated.

        The headline addition is _fix_inject_missing_shim: when MetaEditor still
        reports 'undeclared identifier X' for a legacy MQL4 symbol, it injects
        that symbol's definition (shared with convert.SHIM_DEFS) - collapsing
        the error 256 -> 149/152 cascade at repair time as a safety net for
        anything convert.py did not pre-inject.
"""
from __future__ import annotations
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import re
from textio import count_errors, write_source_text
try:
    # Share the single source of truth for the compat definitions.
    from convert import SHIM_DEFS as _SHIM_DEFS, _SHIM_ORDER, _SHIM_DEPS
except Exception:  # pragma: no cover - convert always ships alongside
    _SHIM_DEFS, _SHIM_ORDER, _SHIM_DEPS = {}, [], {}

MODULE_VERSION = "2.3.0"


# ---------------------------------------------------------------------------
# Original v2.0.0 fixers (unchanged behavior)
# ---------------------------------------------------------------------------
def _fix_deprecated_globals(code, log):
    ch = []
    if re.search(r"'Digits'|undeclared identifier.*Digits", log):
        code, n = re.subn(r"(?<![\w.])Digits(?!\s*\()", "_Digits", code)
        if n: ch.append(f"Digits->_Digits x{n}")
    if re.search(r"'Point'|undeclared identifier.*Point", log):
        code, n = re.subn(r"(?<![\w.])Point(?!\s*\()", "_Point", code)
        if n: ch.append(f"Point->_Point x{n}")
    return code, ch

def _fix_removed_funcs(code, log):
    ch = []
    if re.search(r"IndicatorShortName", log):
        code, n = re.subn(r"\bIndicatorShortName\s*\(",
                          "IndicatorSetString(INDICATOR_SHORTNAME,", code)
        if n: ch.append(f"IndicatorShortName x{n}")
    return code, ch

def _fix_loop_scope(code, log):
    ch = []
    m = re.search(r"undeclared identifier.*?'([A-Za-z_]\w*)'", log)
    if not m: return code, ch
    var = m.group(1)
    pat = re.compile(r"for\s*\(\s*int\s+"+re.escape(var)+r"\s*=")
    if pat.search(code):
        code = pat.sub(f"for({var}=", code, count=1)
        code = re.sub(r"([ \t]*)for\("+re.escape(var)+r"=",
                      r"\1int "+var+r";\n\1for("+var+"=", code, count=1)
        ch.append(f"hoist loop var {var}")
    return code, ch

def _fix_void_return(code, log):
    ch = []
    if _has_err(log, "167") or re.search(r"void function returns a value", log, re.I):
        # The compiler does not reliably print the function name. Repair every
        # balanced void body, not just OnDeinit.
        starts=list(re.finditer(r"\bvoid\s+[A-Za-z_]\w*\s*\([^;{}]*\)\s*\{",code))
        for m in reversed(starts):
            ob=code.find("{",m.start(),m.end()); depth=0; cb=-1
            for i in range(ob,len(code)):
                if code[i]=="{": depth+=1
                elif code[i]=="}":
                    depth-=1
                    if depth==0: cb=i; break
            if cb<0: continue
            body=code[ob+1:cb]
            fixed,n1=re.subn(r"\breturn\s*\([^;]*\)\s*;","return;",body)
            fixed,n2=re.subn(r"\breturn\s+[^;]+;","return;",fixed)
            if n1+n2:
                code=code[:ob+1]+fixed+code[cb:]; ch.append(f"void return x{n1+n2}")
    return code, ch

def _fix_orphan_oninit_return(code, log):
    """Move a global return(0) back inside an prematurely closed OnInit body."""
    if not (_has_err(log,"175") or _has_err(log,"117")):
        return code, []
    m=re.search(r"\bint\s+OnInit\s*\([^;{}]*\)\s*\{",code)
    if not m: return code, []
    ob=code.find("{",m.start(),m.end()); depth=0; cb=-1
    for i in range(ob,len(code)):
        if code[i]=="{": depth+=1
        elif code[i]=="}":
            depth-=1
            if depth==0: cb=i; break
    if cb<0: return code, []
    nextfn=re.search(r"\b(?:int|void|double|bool|string)\s+[A-Za-z_]\w*\s*\(",code[cb+1:])
    end=(cb+1+nextfn.start()) if nextfn else len(code)
    tail=code[cb+1:end]
    rm=re.search(r"\breturn\s*\(\s*0\s*\)\s*;",tail)
    if rm:
        a=cb+1+rm.start(); b=cb+1+rm.end()
        code=code[:cb]+"   return(0);\n"+code[cb:a]+code[b:]
        return code,["moved orphan OnInit return"]
    # No orphan return: add the required return only when compiler says a
    # control path is missing it.
    if _has_err(log,"117") and not re.search(r"\breturn\s*\(",code[ob+1:cb]):
        code=code[:cb]+"   return(0);\n"+code[cb:]
        return code,["added OnInit return"]
    return code,[]

def _fix_oncalculate_bare_return(code, log):
    if not _has_err(log,"121"): return code,[]
    m=re.search(r"\bint\s+OnCalculate\b[^{]*\{",code,re.S)
    if not m: return code,[]
    ob=m.end()-1; depth=0; cb=-1
    for i in range(ob,len(code)):
        if code[i]=="{": depth+=1
        elif code[i]=="}":
            depth-=1
            if depth==0: cb=i; break
    if cb<0:return code,[]
    body=code[ob+1:cb]
    body,n=re.subn(r"\breturn\s*;","return(rates_total);",body)
    if n:return code[:ob+1]+body+code[cb:],[f"OnCalculate bare return x{n}"]
    return code,[]


# ---------------------------------------------------------------------------
# v2.1.0 adaptive fixers (merged from post_repair) - all LOG-GATED
# ---------------------------------------------------------------------------
def _undeclared(log):
    return set(re.findall(r"undeclared identifier '([^']+)'", log or "", re.I))

def _has_err(log, code):
    return bool(re.search(rf"\berror\s+{code}\s*:", log or "", re.I)) or \
           bool(re.search(rf"error {code}:", log or "", re.I))

def _find_insert_point(code):
    last = 0
    for m in re.finditer(r"(?m)^[ \t]*#(?:property|include)\b[^\n]*\n", code):
        last = m.end()
    return last

def _fix_inject_missing_shim(code, log):
    """Root fix for undeclared legacy MQL4 API: inject its definition inline.
    Kills the 256 -> 149 -> 152 cascade. Shares defs with convert.py."""
    need = set(s for s in _undeclared(log) if s in _SHIM_DEFS)
    if not need:
        return code, []
    # Pull in dependencies (e.g. iBandsOnArray needs iMAOnArray/iStdDevOnArray).
    for sym in list(need):
        for dep in _SHIM_DEPS.get(sym, []):
            need.add(dep)
    # Only inject a symbol whose definition is NOT already present in the file.
    def _already_defined(sym):
        if sym == "EMPTY":
            return "#define EMPTY" in code
        return re.search(r"(?:void|double|int|string|bool)\s+"+re.escape(sym)+r"\s*\(", code) is not None
    add = [s for s in _SHIM_ORDER if s in need and not _already_defined(s)]
    if not add:
        return code, []
    block = ("\n//====== MQL ONE repair-injected compat ======\n"
             + "\n".join(_SHIM_DEFS[s] for s in add) + "\n")
    pos = _find_insert_point(code)
    code = code[:pos] + block + code[pos:]
    return code, [f"injected shim: {', '.join(add)}"]

# ---------------------------------------------------------------------------
# error 179 ("initialization skipped by 'case' label") -> wrap each case body
# in braces.  Brace-, string- and comment-aware so it NEVER unbalances a file.
#
# History: the previous implementation inserted a "{" after every case/default
# label but only added a "}" after each "break;".  Any switch whose cases used
# "return(...)" or fell through (no "break;") - including the compat shims this
# very pipeline injects (ArrayCopySeries, MarketInfo, ObjectSet) - was left with
# unmatched "{", producing MQL5 error 161 ("unexpected end of program") and
# error 133, which cascaded to error 209 and pushed otherwise-fine files into
# the AI queue.  This version pairs every brace and reverts on any imbalance.
# ---------------------------------------------------------------------------
def _cl_split_top_level(body):
    """Split a switch body on TOP-LEVEL case/default labels, ignoring nested
    braces, strings, char literals and comments.
    Returns (prefix, [(label_text, segment_body), ...])."""
    i, n = 0, len(body)
    depth = 0
    chunks = []
    cur = None
    pend = None
    while i < n:
        c = body[i]
        if c == '"' or c == "'":
            q = c; i += 1
            while i < n and body[i] != q:
                if body[i] == '\\': i += 1
                i += 1
            i += 1; continue
        if c == '/' and i + 1 < n and body[i + 1] == '/':
            while i < n and body[i] != '\n': i += 1
            continue
        if c == '/' and i + 1 < n and body[i + 1] == '*':
            i += 2
            while i + 1 < n and not (body[i] == '*' and body[i + 1] == '/'): i += 1
            i += 2; continue
        if c == '{': depth += 1; i += 1; continue
        if c == '}': depth -= 1; i += 1; continue
        if depth == 0:
            m = re.match(r"(case\b[^:?{}]*:|default\s*:)", body[i:])
            if m:
                if pend is None: pend = i
                if cur is not None:
                    cur[2] = i; chunks.append(cur)
                cur = [m.group(0), i + m.end(), None]
                i += m.end(); continue
        i += 1
    if cur is not None:
        cur[2] = n; chunks.append(cur)
    if pend is None: pend = n
    return body[:pend], [(l, body[s:e]) for (l, s, e) in chunks]


def _cl_already_wrapped(body):
    """True if body is already exactly one top-level { ... } block."""
    s = body.strip()
    if not (s.startswith('{') and s.endswith('}')):
        return False
    depth = 0
    for k, ch in enumerate(s):
        if ch == '{': depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return k == len(s) - 1
    return False


def _cl_wrap_bodies(inner):
    prefix, chunks = _cl_split_top_level(inner)
    if not chunks:
        return inner
    out = [prefix]
    i = 0
    while i < len(chunks):
        # group consecutive labels that share a body (fall-through stacking)
        labels = [chunks[i][0]]
        j = i
        while chunks[j][1].strip() == '' and j + 1 < len(chunks):
            j += 1
            labels.append(chunks[j][0])
        body = chunks[j][1]
        if _cl_already_wrapped(body):
            out.append(''.join(labels) + body)
        else:
            out.append(''.join(labels) + '{' + body.rstrip() + '}')
            out.append(body[len(body.rstrip()):])
        i = j + 1
    return ''.join(out)


def _cl_switch_blocks(code):
    """(open_index, close_index) for each OUTERMOST switch(...){...} block."""
    blocks = []
    for m in re.finditer(r"\bswitch\s*\([^;{}]*\)\s*\{", code):
        ob = code.rfind('{', m.start(), m.end())
        depth = 0; i = ob
        while i < len(code):
            ch = code[i]
            if ch == '"' or ch == "'":
                q = ch; i += 1
                while i < len(code) and code[i] != q:
                    if code[i] == '\\': i += 1
                    i += 1
            elif ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    blocks.append((ob, i)); break
            i += 1
    outer = [b for b in blocks
             if not any(o[0] < b[0] and b[1] <= o[1] for o in blocks if o is not b)]
    outer.sort()
    return outer


def _fix_case_label_init(code, log):
    """error 179: initialization skipped by 'case' label -> wrap body in { }."""
    if not _has_err(log, "179"):
        return code, []
    # Prefer hoisting case-local initializers.  Wrapping each case fixes 179 but
    # changes scope: a name declared in the first case becomes undeclared in all
    # following cases (exactly what happened to TimeFrameStr in the field logs).
    # Hoisting preserves the MQL4 switch semantics.
    hoisted = 0
    for ob, cb in reversed(_cl_switch_blocks(code)):
        inner = code[ob + 1:cb]
        decls = re.findall(r"(?m)(?:case\b[^:]*:|default\s*:)[^{\n]*\s*"
                           r"(string|int|double|bool|datetime)\s+([A-Za-z_]\w*)\s*=",
                           inner)
        if not decls: continue
        unique=[]
        for typ,name in decls:
            if name not in [n for _,n in unique]: unique.append((typ,name))
        fixed=inner
        for typ,name in unique:
            fixed=re.sub(r"\b"+typ+r"\s+"+re.escape(name)+r"\s*=", name+"=", fixed)
        prefix="".join(f"{typ} {name};\n" for typ,name in unique)
        switch_start=code.rfind("switch",0,ob)
        code=code[:switch_start]+prefix+code[switch_start:ob+1]+fixed+code[cb:]
        hoisted += len(unique)
    if hoisted:
        return code, [f"hoisted case declarations x{hoisted}"]
    blocks = _cl_switch_blocks(code)
    if not blocks:
        return code, []
    n = 0
    res = []
    last = 0
    for (ob, cb) in blocks:
        inner = code[ob + 1:cb]
        # only act when a label is directly followed by a bare statement
        if not re.search(r"(case\b[^:?{}]*:|default\s*:)\s*[^{\s]", inner):
            continue
        fixed = _cl_wrap_bodies(inner)
        # each block rewrite must be self-balanced; else skip just this block
        if fixed == inner or \
           (fixed.count('{') - fixed.count('}')) != (inner.count('{') - inner.count('}')):
            continue
        res.append(code[last:ob + 1])
        res.append(fixed)
        last = cb
        n += 1
    res.append(code[last:])
    new = ''.join(res)
    if not n:
        return code, []
    # backstop: never emit a file with worse brace balance than we received
    if (new.count('{') - new.count('}')) != (code.count('{') - code.count('}')):
        return code, []
    return new, [f"case-label braces x{n}"]

def _fix_global_scope(code, log):
    """error 175: expression on global scope -> hoist name=Inp_name; into OnInit()."""
    if not _has_err(log, "175"):
        return code, []
    assigns = re.findall(r"(?m)^\s*([A-Za-z_]\w*)\s*=\s*Inp_\1\s*;\s*$", code)
    if not assigns:
        return code, []
    code = re.sub(r"(?m)^\s*([A-Za-z_]\w*)\s*=\s*Inp_\1\s*;\s*$\n?", "", code)
    block = "".join(f"   {a}=Inp_{a};\n" for a in assigns)
    m = re.search(r"(int\s+OnInit\s*\([^)]*\)\s*\{)", code)
    if m:
        code = code[:m.end()] + "\n" + block + code[m.end():]
        return code, [f"hoisted {len(assigns)} init assignment(s) into OnInit()"]
    return code, []

def _fix_enum_cast(code, log):
    """error 262: cannot convert enum -> add (ENUM_TIMEFRAMES) on iXxx tf arg."""
    if not _has_err(log, "262"):
        return code, []
    def cast(m):
        return f"{m.group(1)}(ENUM_TIMEFRAMES){m.group(2)}"
    new, n = re.subn(r"(\bi[A-Z]\w*\s*\(\s*[^,]+,\s*)(?!\(ENUM_TIMEFRAMES\))([A-Za-z_]\w*)",
                     cast, code)
    return (new, [f"(ENUM_TIMEFRAMES) casts x{n}"]) if n else (code, [])

def _fix_control_paths(code, log):
    """error 117: not all control paths return a value -> append a safe return."""
    if not _has_err(log, "117"):
        return code, []
    n = 0
    def add(m):
        nonlocal n
        body = m.group(0)
        if re.search(r"return\s*[^;]*;\s*\}\s*$", body):
            return body
        n += 1
        ret = "return(false);" if body.lstrip().startswith("bool") else "return(0);"
        return body[:-1] + "   " + ret + "\n}"
    new = re.sub(r"(?s)\b(?:int|double|bool)\s+\w+\s*\([^;{}]*\)\s*\{.*?\n\}", add, code)
    return (new, [f"control-path returns x{n}"]) if n else (code, [])


_FIXERS = [
    _fix_inject_missing_shim,   # root-cause: undeclared legacy API
    _fix_deprecated_globals,    # Digits/Point
    _fix_removed_funcs,         # IndicatorShortName
    _fix_loop_scope,            # loop var scope
    _fix_void_return,           # OnDeinit returns
    _fix_orphan_oninit_return,  # global return / missing OnInit return
    _fix_oncalculate_bare_return,
    _fix_case_label_init,       # 179
    _fix_global_scope,          # 175
    _fix_enum_cast,             # 262
    _fix_control_paths,         # 117
]


def apply_rules(name, code, log_text, passno=1):
    ch = []
    for fx in _FIXERS:
        try:
            code, c = fx(code, log_text or "")
        except Exception:
            c = []
        ch += c
    return code, ch


def looks_dependency_blocked(log_text):
    m = re.search(r"cannot open (?:the )?(?:include|program) file [\"']?"
                  r"([^\"'\n]+)", log_text or "", re.I)
    return m.group(1).strip() if m else None


def repair(name, code, log_text, compile_fn, meta, mq5_path, passes=3):
    best_code = code
    best_err, best_warn = count_errors(log_text or "")
    best_err = 9999 if best_err is None else best_err
    for _ in range(passes):
        cand, ch = apply_rules(name, best_code, log_text)
        if cand == best_code or not ch: break
        write_source_text(mq5_path, cand)
        ok, e, w, newlog = compile_fn(meta, mq5_path)
        e = 0 if (ok and e is None) else (e if e is not None else 9999)
        if ok or e < best_err:
            best_code, log_text, best_err, best_warn = cand, newlog, e, w
            if ok or best_err == 0:
                write_source_text(mq5_path, best_code)
                return "REPAIRED_COMPILED", 0, best_warn, ch
        else:
            break
    write_source_text(mq5_path, best_code)
    dep = looks_dependency_blocked(log_text)
    if dep: return "BLOCKED_DEPENDENCY:"+dep, best_err, best_warn, []
    return "NEEDS_REVIEW", best_err, best_warn, []
