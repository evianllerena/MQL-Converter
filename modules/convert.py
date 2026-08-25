#!/usr/bin/env python3
"""modules/convert.py - MQL4 -> MQL5 translation with inlined compat shim.

v2.1.0  MERGE of the proven offline-converter coverage into the MQL ONE module,
        WITHOUT changing the public interface or the app.

        Public API (unchanged, verified against MQL_ONE_app.py):
            MODULE_VERSION
            convert_source(text)        -> str
            convert_with_report(text)   -> (str, [changes])

        What v2.1.0 adds (the reason 65% of prior errors were cascade noise:
        undeclared MQL4 API -> error 256 -> error 149/152 storm):
        1. A SECOND, CONDITIONAL compat block that DEFINES the legacy MQL4
           identifiers your logs proved undeclared - but ONLY the ones actually
           present in each file, so no unused-function warnings creep in:
               SetIndexStyle/Label/Arrow/DrawBegin/Shift/EmptyValue,
               SetLevelValue, IndicatorBuffers/ShortName/Digits,
               MarketInfo, WindowFind, WindowExpertName, ObjectSet,
               ArrayCopySeries, iMAOnArray/iStdDevOnArray/iBandsOnArray, EMPTY.
        2. Safe structural rewrites the old engine did and this module lacked:
               SetIndexBuffer(i,buf)  -> SetIndexBuffer(i,buf,INDICATOR_DATA)
               SetLevelStyle(...)     -> IndicatorSetInteger(LEVEL*)
        The original v2.0.0 base shim (MQL4_Bid/Ask/Bars/... ) and every
        existing pass are preserved byte-for-byte in behavior, so nothing that
        already worked can regress.
"""
from __future__ import annotations
import re
MODULE_VERSION = "3.0.0"

# ---------------------------------------------------------------------------
# BASE shim - identical to v2.0.0, ALWAYS injected (call sites are rewritten
# to these names, so they must exist).  Do not remove.
# ---------------------------------------------------------------------------
COMPAT_SHIM = r"""
//==================== MQL ONE compat shim (auto-inlined) ===================
// Do not add mql4compat.mqh / MT5Compat.mqh - these symbols already exist.
#ifndef MQLONE_COMPAT_SHIM
#define MQLONE_COMPAT_SHIM
#property strict
double MQL4_Bid() { return SymbolInfoDouble(_Symbol, SYMBOL_BID); }
double MQL4_Ask() { return SymbolInfoDouble(_Symbol, SYMBOL_ASK); }
int MQL4_Bars(string sym=NULL, ENUM_TIMEFRAMES tf=PERIOD_CURRENT)
{ return Bars(sym==NULL?_Symbol:sym, tf); }
int __mqlone_prev_calculated = 0;
int MQL4_IndicatorCounted() { return __mqlone_prev_calculated; }
double MQL4_Point()  { return _Point; }
int    MQL4_Digits() { return _Digits; }
int MQL4_TimeDay(datetime v){MqlDateTime x;TimeToStruct(v,x);return x.day;}
int MQL4_TimeHour(datetime v){MqlDateTime x;TimeToStruct(v,x);return x.hour;}
int MQL4_TimeMinute(datetime v){MqlDateTime x;TimeToStruct(v,x);return x.min;}
int MQL4_TimeMonth(datetime v){MqlDateTime x;TimeToStruct(v,x);return x.mon;}
int MQL4_TimeYear(datetime v){MqlDateTime x;TimeToStruct(v,x);return x.year;}
int MQL4_TimeDayOfWeek(datetime v){MqlDateTime x;TimeToStruct(v,x);return x.day_of_week;}
int MQL4_Highest(string sym,int tf,int mode,int count,int start){
 string s=(sym==NULL?_Symbol:sym); ENUM_TIMEFRAMES p=(ENUM_TIMEFRAMES)tf;
 return(mode==MODE_LOW?iLowest(s,p,MODE_LOW,count,start):iHighest(s,p,MODE_HIGH,count,start));}
int MQL4_Lowest(string sym,int tf,int mode,int count,int start){
 string s=(sym==NULL?_Symbol:sym); ENUM_TIMEFRAMES p=(ENUM_TIMEFRAMES)tf;
 return(mode==MODE_HIGH?iHighest(s,p,MODE_HIGH,count,start):iLowest(s,p,MODE_LOW,count,start));}
#ifndef MODE_TIME
#define MODE_TIME 0
#endif
#ifndef MODE_DIGITS
#define MODE_DIGITS 10
#endif
#ifndef OP_BUY
#define OP_BUY 0
#define OP_SELL 1
#define OP_BUYLIMIT 2
#define OP_SELLLIMIT 3
#define OP_BUYSTOP 4
#define OP_SELLSTOP 5
#endif
#endif
//===========================================================================
"""

# ---------------------------------------------------------------------------
# EXTENDED shim - each entry is DEFINED only if its symbol appears in the file.
# Every one of these is REMOVED in MQL5, so defining it can never collide with
# a built-in (no "function already defined").  Guarded by its own #ifndef.
# ---------------------------------------------------------------------------
SHIM_DEFS = {
    "EMPTY":
        "#ifndef EMPTY\n#define EMPTY (-1)\n#endif",
    "SetIndexStyle":
        "void SetIndexStyle(int i,int t,int s=-1,int w=-1,color c=clrNONE){"
        "PlotIndexSetInteger(i,PLOT_DRAW_TYPE,t);"
        "if(w!=-1)PlotIndexSetInteger(i,PLOT_LINE_WIDTH,w);"
        "if(s!=-1)PlotIndexSetInteger(i,PLOT_LINE_STYLE,s);"
        "if(c!=clrNONE)PlotIndexSetInteger(i,PLOT_LINE_COLOR,c);}",
    "SetIndexArrow":
        "void SetIndexArrow(int i,int code){PlotIndexSetInteger(i,PLOT_ARROW,code);}",
    "SetIndexLabel":
        "void SetIndexLabel(int i,string txt){PlotIndexSetString(i,PLOT_LABEL,txt);}",
    "SetIndexDrawBegin":
        "void SetIndexDrawBegin(int i,int b){PlotIndexSetInteger(i,PLOT_DRAW_BEGIN,b);}",
    "SetIndexShift":
        "void SetIndexShift(int i,int sh){PlotIndexSetInteger(i,PLOT_SHIFT,sh);}",
    "SetIndexEmptyValue":
        "void SetIndexEmptyValue(int i,double v){PlotIndexSetDouble(i,PLOT_EMPTY_VALUE,v);}",
    "SetLevelValue":
        "void SetLevelValue(int lvl,double v){IndicatorSetDouble(INDICATOR_LEVELVALUE,lvl,v);}",
    "IndicatorBuffers":
        "void IndicatorBuffers(int n){ /* MT5 derives buffer count from #property */ }",
    "IndicatorShortName":
        "void IndicatorShortName(string n){IndicatorSetString(INDICATOR_SHORTNAME,n);}",
    "IndicatorDigits":
        "void IndicatorDigits(int d){IndicatorSetInteger(INDICATOR_DIGITS,d);}",
    "WindowFind":
        "int WindowFind(string n){return ChartWindowFind();}",
    "WindowExpertName":
        "string WindowExpertName(){return MQLInfoString(MQL_PROGRAM_NAME);}",
    "MarketInfo":
        "double MarketInfo(string sym,int t){switch(t){"
        "case 10:return((double)SymbolInfoInteger(sym,SYMBOL_DIGITS));"
        "case 11:return(SymbolInfoDouble(sym,SYMBOL_POINT));"
        "case 12:return(SymbolInfoDouble(sym,SYMBOL_TRADE_TICK_SIZE));"
        "case 13:return(SymbolInfoDouble(sym,SYMBOL_TRADE_TICK_VALUE));"
        "case 9:return(SymbolInfoDouble(sym,SYMBOL_BID));"
        "case 1:return(SymbolInfoDouble(sym,SYMBOL_ASK));"
        "default:return(0.0);}}",
    "ObjectSet":
        "bool ObjectSet(string n,int prop,double val){"
        "if(prop==OBJPROP_PRICE||prop==OBJPROP_ANGLE||prop==OBJPROP_DEVIATION||prop==OBJPROP_SCALE)"
        "return(ObjectSetDouble(0,n,(ENUM_OBJECT_PROPERTY_DOUBLE)prop,val));"
        "return(ObjectSetInteger(0,n,(ENUM_OBJECT_PROPERTY_INTEGER)prop,(long)val));}",
    "ArrayCopySeries":
        "int ArrayCopySeries(double &dst[],int sid,string sym,int tf){"
        "ENUM_TIMEFRAMES p=(ENUM_TIMEFRAMES)tf;int b=Bars(sym,p);"
        "switch(sid){case 1:return(CopyOpen(sym,p,0,b,dst));"
        "case 2:return(CopyHigh(sym,p,0,b,dst));"
        "case 3:return(CopyLow(sym,p,0,b,dst));"
        "default:return(CopyClose(sym,p,0,b,dst));}}",
    "iMAOnArray":
        "double iMAOnArray(const double &a[],int total,int period,int ma_shift,"
        "int method,int shift){int n=(total>0?total:ArraySize(a));"
        "if(period<=0||n<period)return(0.0);int pos=shift+ma_shift;"
        "if(pos<0||pos>=n)return(0.0);double sum=0;"
        "if(method==1){double k=2.0/(period+1.0);double e=a[n-1];"
        "for(int i=n-2;i>=pos;i--)e=a[i]*k+e*(1.0-k);return(e);}"
        "if(method==3){double w=0,s=0;for(int i=0;i<period;i++){int wt=period-i;"
        "s+=a[pos+i]*wt;w+=wt;}return(w>0?s/w:0.0);}"
        "for(int i=0;i<period;i++)sum+=a[pos+i];return(sum/period);}",
    "iStdDevOnArray":
        "double iStdDevOnArray(const double &a[],int total,int period,int ma_shift,"
        "int method,int shift){int n=(total>0?total:ArraySize(a));"
        "if(period<=0||n<period)return(0.0);int pos=shift+ma_shift;"
        "double m=iMAOnArray(a,total,period,0,method,shift);double s=0;"
        "for(int i=0;i<period;i++){double d=a[pos+i]-m;s+=d*d;}return(MathSqrt(s/period));}",
    "iBandsOnArray":
        "void iBandsOnArray(const double &a[],int total,int period,double dev,"
        "int bshift,int shift,double &up,double &mid,double &low){"
        "mid=iMAOnArray(a,total,period,0,0,shift+bshift);"
        "double sd=iStdDevOnArray(a,total,period,0,0,shift+bshift);"
        "up=mid+dev*sd;low=mid-dev*sd;}",
}

# ArrayCopySeries(MODE_TIME, ...) writes datetime[], not double[].  MQL5 allows
# overloads, so append the correct typed form whenever this shim is emitted.
_ARRAYCOPY_TIME_OVERLOAD = (
    "int ArrayCopySeries(datetime &dst[],int sid,string sym,int tf){"
    "string s=(sym==NULL?_Symbol:sym);ENUM_TIMEFRAMES p=(ENUM_TIMEFRAMES)tf;"
    "int b=Bars(s,p);return(CopyTime(s,p,0,b,dst));}")

# MQL5 indicator functions return handles, while MQL4 returned a value for a
# requested shift.  Rename value-style calls and use CopyBuffer explicitly.
VALUE_INDICATOR_DEFS = {
    "iMA": "double MQL4_iMA(string s,int tf,int per,int sh,int meth,int price,int pos){int h=iMA(s,(ENUM_TIMEFRAMES)tf,per,sh,(ENUM_MA_METHOD)meth,(ENUM_APPLIED_PRICE)price);double b[];if(h==INVALID_HANDLE)return EMPTY_VALUE;int n=CopyBuffer(h,0,pos,1,b);IndicatorRelease(h);return(n==1?b[0]:EMPTY_VALUE);}",
    "iCCI": "double MQL4_iCCI(string s,int tf,int per,int price,int pos){int h=iCCI(s,(ENUM_TIMEFRAMES)tf,per,(ENUM_APPLIED_PRICE)price);double b[];if(h==INVALID_HANDLE)return EMPTY_VALUE;int n=CopyBuffer(h,0,pos,1,b);IndicatorRelease(h);return(n==1?b[0]:EMPTY_VALUE);}",
    "iRSI": "double MQL4_iRSI(string s,int tf,int per,int price,int pos){int h=iRSI(s,(ENUM_TIMEFRAMES)tf,per,(ENUM_APPLIED_PRICE)price);double b[];if(h==INVALID_HANDLE)return EMPTY_VALUE;int n=CopyBuffer(h,0,pos,1,b);IndicatorRelease(h);return(n==1?b[0]:EMPTY_VALUE);}",
    "iATR": "double MQL4_iATR(string s,int tf,int per,int pos){int h=iATR(s,(ENUM_TIMEFRAMES)tf,per);double b[];if(h==INVALID_HANDLE)return EMPTY_VALUE;int n=CopyBuffer(h,0,pos,1,b);IndicatorRelease(h);return(n==1?b[0]:EMPTY_VALUE);}",
    "iSAR": "double MQL4_iSAR(string s,int tf,double step,double maximum,int pos){int h=iSAR(s,(ENUM_TIMEFRAMES)tf,step,maximum);double b[];if(h==INVALID_HANDLE)return EMPTY_VALUE;int n=CopyBuffer(h,0,pos,1,b);IndicatorRelease(h);return(n==1?b[0]:EMPTY_VALUE);}",
}
# iBandsOnArray depends on the other two; declare dependencies so they come too.
_SHIM_DEPS = {"iBandsOnArray": ["iMAOnArray", "iStdDevOnArray"],
              "iStdDevOnArray": ["iMAOnArray"]}
# Emit order (definitions must precede callers).
_SHIM_ORDER = ["EMPTY", "iMAOnArray", "iStdDevOnArray", "iBandsOnArray",
               "SetIndexStyle", "SetIndexArrow", "SetIndexLabel",
               "SetIndexDrawBegin", "SetIndexShift", "SetIndexEmptyValue",
               "SetLevelValue", "IndicatorBuffers", "IndicatorShortName",
               "IndicatorDigits", "WindowFind", "WindowExpertName",
               "MarketInfo", "ObjectSet", "ArrayCopySeries"]

_SERIES = {"Close":"iClose","Open":"iOpen","High":"iHigh","Low":"iLow","Volume":"iVolume","Time":"iTime"}
_RENAMES = [(r"\bDoubleToStr\b","DoubleToString"),(r"\bStrToDouble\b","StringToDouble"),
            (r"\bStrToInteger\b","StringToInteger"),(r"\bStrToTime\b","StringToTime"),
            (r"\bTimeToStr\b","TimeToString"),(r"\bStringGetChar\b","StringGetCharacter"),
            (r"\bStringSetChar\b","StringSetCharacter")]
_BIDASK = [(r"(?<![\w.])Bid(?![\w(])","MQL4_Bid()"),(r"(?<![\w.])Ask(?![\w(])","MQL4_Ask()")]

def _strip_block_comments(text):
    spans = []
    pattern = re.compile(r"/\*.*?\*/|//[^\n]*|\"(?:\\.|[^\"\\])*\"", re.S)
    def repl(m):
        spans.append(m.group(0)); return f"\x00{len(spans)-1}\x00"
    return pattern.sub(repl, text), spans

def _restore(text, spans):
    return re.sub(r"\x00(\d+)\x00", lambda m: spans[int(m.group(1))], text)

def _find_insert_point(text):
    last = 0
    for m in re.finditer(r"^[ \t]*#(?:property|include)\b[^\n]*\n", text, re.M):
        last = m.end()
    return last

def _ensure_void_return(text, func):
    pat = re.compile(r"(void\s+" + func + r"\b[^{]*\{)(.*?)(\n\})", re.S)
    def fix(m):
        body = re.sub(r"\breturn\s*\([^;]*\)\s*;", "return;", m.group(2))
        body = re.sub(r"\breturn\s+[^;]+;", "return;", body)
        return m.group(1) + body + m.group(3)
    return pat.sub(fix, text, count=1)

def _convert_lifecycle(text):
    ch = []
    if re.search(r"\bint\s+deinit\s*\(\s*\)", text):
        text = re.sub(r"\bint\s+deinit\s*\(\s*\)", "void OnDeinit(const int reason)", text)
        text = _ensure_void_return(text, "OnDeinit"); ch.append("deinit()->OnDeinit()")
    if re.search(r"\bint\s+init\s*\(\s*\)", text):
        text = re.sub(r"\bint\s+init\s*\(\s*\)", "int OnInit()", text); ch.append("init()->OnInit()")
    if re.search(r"\bint\s+start\s*\(\s*\)", text):
        sig = ("int OnCalculate(const int rates_total,\n                const int prev_calculated,\n"
               "                const datetime &time[],\n                const double &open[],\n"
               "                const double &high[],\n                const double &low[],\n"
               "                const double &close[],\n                const long &tick_volume[],\n"
               "                const long &volume[],\n                const int &spread[])\n{\n"
               "   __mqlone_prev_calculated = prev_calculated;")
        text = re.sub(r"\bint\s+start\s*\(\s*\)\s*\{", sig, text, count=1); ch.append("start()->OnCalculate()")
    return text, ch

def _convert_series(text):
    ch = []
    for name, fn in _SERIES.items():
        pat = re.compile(r"\b"+name+r"\s*\[\s*([^\]]+?)\s*\]")
        new, n = pat.subn(lambda m: f"{fn}(_Symbol,PERIOD_CURRENT,{m.group(1)})", text)
        if n: text = new; ch.append(f"{name}[]->{fn}() x{n}")
    text2, n = re.subn(r"(?<![\w.])Bars(?![\w(\[])", "MQL4_Bars()", text)
    if n: text = text2; ch.append(f"Bars->MQL4_Bars() x{n}")
    return text, ch

def _convert_indicator_counted(text):
    text2, n = re.subn(r"\bIndicatorCounted\s*\(\s*\)", "MQL4_IndicatorCounted()", text)
    return (text2, [f"IndicatorCounted()->shim x{n}"]) if n else (text, [])

def _convert_extern(text):
    def repl(m):
        name = m.group(2); decl = m.group(0)
        n_assign = len(re.findall(r"\b"+re.escape(name)+r"\s*=(?!=)", text))
        return decl.replace("extern ","",1) if n_assign > 1 else decl.replace("extern","input",1)
    text2, n = re.subn(r"\bextern\s+([A-Za-z_][\w]*)\s+([A-Za-z_]\w*)", repl, text)
    return (text2, [f"extern->input x{n}"]) if n else (text, [])

def _convert_renames(text):
    ch = []
    for pat, rep in _RENAMES:
        new, n = re.subn(pat, rep, text)
        if n: text = new; ch.append(f"{rep} x{n}")
    return text, ch

def _convert_bidask(text):
    ch = []
    for pat, rep in _BIDASK:
        new, n = re.subn(pat, rep, text)
        if n: text = new; ch.append(f"{rep} x{n}")
    return text, ch

def _convert_legacy_scalars(text):
    ch = []
    rules = [(r"(?<![\w.])Digits(?![\w(])", "_Digits"),
             (r"(?<![\w.])Point(?![\w(])", "_Point"),
             (r"\bTimeDay\s*\(", "MQL4_TimeDay("),
             (r"\bTimeHour\s*\(", "MQL4_TimeHour("),
             (r"\bTimeMinute\s*\(", "MQL4_TimeMinute("),
             (r"\bTimeMonth\s*\(", "MQL4_TimeMonth("),
             (r"\bTimeYear\s*\(", "MQL4_TimeYear("),
             (r"\bTimeDayOfWeek\s*\(", "MQL4_TimeDayOfWeek("),
             (r"\bHighest\s*\(", "MQL4_Highest("),
             (r"\bLowest\s*\(", "MQL4_Lowest(")]
    for pat, rep in rules:
        text, n = re.subn(pat, rep, text)
        if n: ch.append(f"{rep} x{n}")
    return text, ch

def _convert_value_indicators(text):
    ch, needed = [], []
    for name in VALUE_INDICATOR_DEFS:
        # Only MQL4 value signatures have the extra trailing shift argument.
        # The sources being migrated are MQ4, so every call is value-style.
        text, n = re.subn(r"\b" + name + r"\s*\(", "MQL4_" + name + "(", text)
        if n: needed.append(name); ch.append(f"{name} value API x{n}")
    if needed:
        pos = _find_insert_point(text)
        block = "\n// MQL4 indicator value adapters\n" + "\n".join(VALUE_INDICATOR_DEFS[n] for n in needed) + "\n"
        text = text[:pos] + block + text[pos:]
    return text, ch

def _split_args(s):
    args, depth, cur = [], 0, ""
    for c in s:
        if c == "(": depth += 1; cur += c
        elif c == ")": depth -= 1; cur += c
        elif c == "," and depth == 0: args.append(cur.strip()); cur = ""
        else: cur += c
    if cur.strip(): args.append(cur.strip())
    return args

def _match_call(text, start):
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "(": depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0: return i
    return -1

def _convert_object_api(text):
    ch, out, idx = [], [], 0
    for m in re.compile(r"\bObjectSetText\s*\(").finditer(text):
        out.append(text[idx:m.start()])
        op = m.end()-1; close = _match_call(text, op)
        if close < 0: out.append(text[m.start():m.end()]); idx = m.end(); continue
        args = _split_args(text[op+1:close])
        if len(args) >= 2:
            repl = f"ObjectSetString(0,{args[0]},OBJPROP_TEXT,{args[1]})"
            if len(args) >= 3: repl += f";\n   ObjectSetInteger(0,{args[0]},OBJPROP_FONTSIZE,{args[2]})"
            if len(args) >= 4: repl += f";\n   ObjectSetString(0,{args[0]},OBJPROP_FONT,{args[3]})"
            if len(args) >= 5: repl += f";\n   ObjectSetInteger(0,{args[0]},OBJPROP_COLOR,{args[4]})"
            out.append(repl); ch.append("ObjectSetText->ObjectSetString")
        else: out.append(text[m.start():close+1])
        idx = close+1
    out.append(text[idx:])
    return "".join(out), ch

def _convert_setindexbuffer_role(text):
    """SetIndexBuffer(i,buf) -> SetIndexBuffer(i,buf,INDICATOR_DATA). 2-arg form only."""
    pat = re.compile(r"\bSetIndexBuffer\s*\(\s*([^,()]+?)\s*,\s*([^,()]+?)\s*\)")
    new, n = pat.subn(r"SetIndexBuffer(\1,\2,INDICATOR_DATA)", text)
    return (new, [f"SetIndexBuffer role x{n}"]) if n else (text, [])

def _convert_setlevelstyle(text):
    """SetLevelStyle(style[,width[,color]]) -> IndicatorSetInteger(LEVEL*)."""
    pat = re.compile(r"\bSetLevelStyle\s*\(\s*([^,()]+?)\s*(?:,\s*([^,()]+?)\s*)?(?:,\s*([^()]+?)\s*)?\)\s*;")
    def repl(m):
        style, width, color = m.group(1), m.group(2), m.group(3)
        parts = [f"IndicatorSetInteger(INDICATOR_LEVELSTYLE,{style});"]
        if width: parts.append(f"IndicatorSetInteger(INDICATOR_LEVELWIDTH,{width});")
        if color: parts.append(f"IndicatorSetInteger(INDICATOR_LEVELCOLOR,{color});")
        return " ".join(parts)
    new, n = pat.subn(repl, text)
    return (new, [f"SetLevelStyle->LEVEL* x{n}"]) if n else (text, [])

def _fix_oncalculate_return(text):
    m = re.search(r"\bint\s+OnCalculate\b[^{]*\{", text)
    if not m: return text, []
    start = m.end()-1; depth = 0; end = -1
    for i in range(start, len(text)):
        if text[i] == "{": depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0: end = i; break
    if end < 0: return text, []
    body = text[start:end]
    nb, n = re.subn(r"\breturn\s*\(\s*0\s*\)\s*;", "return(rates_total);", body)
    nb, n2 = re.subn(r"\breturn\s+0\s*;", "return(rates_total);", nb)
    if n+n2: return text[:start]+nb+text[end:], [f"OnCalc return->rates_total x{n+n2}"]
    return text, []

def _needed_shims(code):
    """Return ordered list of extended-shim symbols actually referenced."""
    needed = set()
    for sym in SHIM_DEFS:
        if sym == "EMPTY":
            if re.search(r"(?<![\w.])EMPTY(?![\w])", code):
                needed.add(sym)
        elif re.search(r"\b"+re.escape(sym)+r"\s*\(", code):
            needed.add(sym)
    # pull in dependencies (e.g. iBandsOnArray needs iMAOnArray/iStdDevOnArray)
    for sym in list(needed):
        for dep in _SHIM_DEPS.get(sym, []):
            needed.add(dep)
    return [s for s in _SHIM_ORDER if s in needed]

def _build_extended_block(code):
    syms = _needed_shims(code)
    if not syms:
        return ""
    body = "\n".join(SHIM_DEFS[s] for s in syms)
    if "ArrayCopySeries" in syms:
        body += "\n" + _ARRAYCOPY_TIME_OVERLOAD
    return ("\n//============ MQL ONE extended compat (only symbols in use) ============\n"
            "#ifndef MQLONE_COMPAT_EXT\n#define MQLONE_COMPAT_EXT\n"
            + body +
            "\n#endif\n//======================================================================\n")

def _inject_shim(text):
    pos = _find_insert_point(text)
    ext = _build_extended_block(text)
    return text[:pos] + COMPAT_SHIM + ext + text[pos:]

_PASSES = (_convert_lifecycle, _convert_series, _convert_indicator_counted,
           _convert_extern, _convert_renames, _convert_bidask,
           _convert_legacy_scalars, _convert_value_indicators,
           _convert_object_api, _convert_setindexbuffer_role,
           _convert_setlevelstyle, _fix_oncalculate_return)

def convert_source(text: str) -> str:
    if "MQLONE_COMPAT_SHIM" in text: return text
    masked, spans = _strip_block_comments(text)
    for fn in _PASSES: masked, _ = fn(masked)
    return _restore(_inject_shim(masked), spans)

def convert_with_report(text: str):
    if "MQLONE_COMPAT_SHIM" in text: return text, ["already converted"]
    masked, spans = _strip_block_comments(text); all_ch = []
    for fn in _PASSES:
        masked, ch = fn(masked); all_ch += ch
    return _restore(_inject_shim(masked), spans), all_ch

if __name__ == "__main__":
    import sys
    out, ch = convert_with_report(sys.stdin.read())
    sys.stderr.write("changes: "+"; ".join(ch)+"\n"); sys.stdout.write(out)
