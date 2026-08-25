#!/usr/bin/env python3
"""modules/textio.py - safe read/write for MQL sources and compiler logs."""
from __future__ import annotations
import re
from pathlib import Path
MODULE_VERSION = "1.0.0"

def read_source_text(p: Path) -> str:
    b = Path(p).read_bytes()
    if b.startswith(b"\xef\xbb\xbf"):
        return b.decode("utf-8-sig", errors="replace")
    if b.count(b"\x00") > max(8, len(b) // 20):
        return b.decode("utf-16", errors="replace").replace("\ufeff", "")
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("latin-1", errors="replace")

def write_source_text(p: Path, text: str):
    text = re.sub(r"\r+\n", "\n", text).replace("\r", "\n")
    Path(p).write_bytes(text.replace("\n", "\r\n").encode("utf-8"))

def normalize_line_endings(p: Path) -> bool:
    try:
        raw = Path(p).read_bytes()
        if b"\r\r" not in raw:
            return False
        write_source_text(p, read_source_text(p))
        return True
    except Exception:
        return False

def read_metaeditor_log(log: Path) -> str:
    log = Path(log)
    if not log.exists():
        return ""
    raw = log.read_bytes()
    if not raw:
        return ""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return raw.decode("utf-16")
        except Exception:
            pass
    if raw[:3] == b"\xef\xbb\xbf":
        return raw.decode("utf-8-sig", errors="replace")
    try:
        text = raw.decode("utf-8")
        if "\x00" not in text:
            return text
    except UnicodeDecodeError:
        pass
    try:
        text = raw.decode("utf-16-le")
        if sum(c.isascii() for c in text[:200]) > len(text[:200]) * 0.6:
            return text
    except Exception:
        pass
    return raw.decode("latin-1", errors="replace")

def count_errors(text: str):
    m = re.search(r"(\d+)\s+error", text or "")
    w = re.search(r"(\d+)\s+warning", text or "")
    return (int(m.group(1)) if m else None,
            int(w.group(1)) if w else None)

def error_lines(text: str, limit=8000) -> str:
    if not text:
        return ""
    lines = [ln for ln in text.splitlines()
             if re.search(r"error|warning", ln, re.I)]
    return ("\n".join(lines) if lines else text)[:limit]
