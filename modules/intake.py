#!/usr/bin/env python3
"""modules/intake.py - watch-folder scan + zip recovery."""
from __future__ import annotations
import struct, zlib, zipfile
from pathlib import Path
MODULE_VERSION = "1.0.0"
DEFAULT_EXTS = {".mq4"}

def scan_watch(folder, exts=DEFAULT_EXTS):
    folder = Path(folder)
    if not folder.exists(): return []
    return [p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in exts]

def _unique(dest_dir, name):
    dest = dest_dir/name; i, stem, suf = 2, Path(name).stem, Path(name).suffix
    while dest.exists(): dest = dest_dir/f"{stem}__{i}{suf}"; i += 1
    return dest

def harvest_zip(zpath, dest_dir, exts=DEFAULT_EXTS):
    zpath, dest_dir = Path(zpath), Path(dest_dir); dest_dir.mkdir(parents=True, exist_ok=True)
    staged, bad = [], 0
    try:
        with zipfile.ZipFile(zpath) as z:
            for m in z.infolist():
                if m.is_dir() or Path(m.filename).suffix.lower() not in exts: continue
                try: data = z.read(m)
                except Exception: bad += 1; continue
                dest = _unique(dest_dir, Path(m.filename).name)
                dest.write_bytes(data); staged.append(dest)
        if bad and not staged: return _tolerant(zpath, dest_dir, exts)
        return staged
    except zipfile.BadZipFile:
        return _tolerant(zpath, dest_dir, exts)
    except Exception:
        return staged

def _tolerant(zpath, dest_dir, exts):
    staged = []
    try: blob = zpath.read_bytes()
    except Exception: return staged
    pos = 0
    while True:
        pos = blob.find(b"PK\x03\x04", pos)
        if pos < 0: break
        try:
            (_,flags,method,_,_,crc,csize,usize,nlen,xlen) = struct.unpack_from("<HHHHHIIIHH",blob,pos+4)
            name_raw = blob[pos+30:pos+30+nlen]; data_off = pos+30+nlen+xlen; pos = data_off+max(csize,0)
            if flags & 0x1: continue
            if flags & 0x8 and csize == 0: continue
            try: fname = name_raw.decode("utf-8")
            except UnicodeDecodeError: fname = name_raw.decode("cp437", errors="replace")
            if fname.endswith("/") or Path(fname).suffix.lower() not in exts: continue
            raw = blob[data_off:data_off+csize]
            if method == 0: data = raw[:usize] if usize else raw
            elif method == 8: data = zlib.decompress(raw, -15)
            else: continue
            if usize and crc and (zlib.crc32(data)&0xFFFFFFFF) != crc: continue
            safe = "".join(c if c.isprintable() and c not in '<>:"/\\|?*' else "_"
                           for c in Path(fname).name) or "member"
            dest = _unique(dest_dir, safe); dest.write_bytes(data); staged.append(dest)
        except Exception:
            pos += 4
    return staged
