"""Slice 1 gate, targeted rerun: which gate inputs can 3755dbf and 53052b8 change?

- 53052b8 changes a pin's type or name only where a placed symbol selects an alternate on a pin
  that is hidden, or sits on a power symbol, or where the alternate is spelled like the pin's own
  name (old code took that alternate's type; KiCad drops it). Any other placed alternate (a
  visible pin on an ordinary part, the usual MCU case) reads identically before and after.
- 3755dbf changes only calls with 0 < stub_length < 0.00005 mm.
Scans every .kicad_sch under the given runs and KiCad's demos for the first, and every record
(.jsonl) of the given runs for the second. Builder scripts that write alternates at run time are
listed separately by grep.
Run (RUN is a run name under work/runs or a path; --run repeats; default: the current run):
    uv run python tools/routing_harness/analysis/slice1/scan_fix_reach.py --run RUN > scan_fix_reach.out
"""

import argparse
import glob
import json
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
from mcp_server_kicad import _cst  # noqa: E402

_ap = argparse.ArgumentParser(description="Which gate inputs can 3755dbf and 53052b8 change? (slice 1 gate)")
_ap.add_argument("--run", action="append",
                 help="run to scan: a name under work/runs or a path; repeatable (default: the current run)")
RUNS = [hpaths.run_dir(r) for r in (_ap.parse_args().run or [hpaths.RUN])]
for _r in RUNS:
    if not _r.is_dir():
        sys.exit(f"run directory not found: {_r}")
ROOTS = [*RUNS, Path(hpaths.KICAD_DEMOS)]


def text(node, name):
    n = node.find(name)
    return n.atoms[1].text if n is not None and len(n.atoms) > 1 else None


def hidden(pin):
    if any(a.text == "hide" for a in pin.atoms[3:]):
        return True
    h = pin.find("hide")
    return h is not None and (len(h.atoms) < 2 or h.atoms[1].text == "yes")


def scan(path):
    if not Path(path).is_file():
        return []
    data = Path(path).read_bytes()
    if b"(alternate" not in data:
        return []
    root = _cst.parse(data).lists[0]
    lsyms = root.find("lib_symbols")
    libs = {s.atoms[1].text: s for s in (lsyms.find_all("symbol") if lsyms is not None else [])}
    out = []
    for s in root.find_all("symbol"):
        sel = {p.atoms[1].text: text(p, "alternate") for p in s.find_all("pin") if p.find("alternate") is not None}
        if not sel:
            continue
        key = text(s, "lib_name") or text(s, "lib_id")
        lib = libs.get(key)
        ref = next((q.atoms[2].text for q in s.find_all("property") if len(q.atoms) > 2 and q.atoms[1].text == "Reference"), "?")
        is_power = lib is not None and lib.find("power") is not None
        for num, alt in sel.items():
            pins = [p for sub in (lib.find_all("symbol") if lib is not None else []) for p in sub.find_all("pin")
                    if text(p, "number") == num]
            for p in pins or [None]:
                hid = p is not None and hidden(p)
                pname = text(p, "name") if p is not None else None
                if hid or is_power or alt == pname or p is None:
                    out.append((ref, num, alt, pname, "hidden" if hid else "", "power" if is_power else "",
                                "self-named" if alt == pname else "", "no lib pin" if p is None else ""))
    return out


seen = 0
for root in ROOTS:
    for f in glob.glob(str(root / "**" / "*.kicad_sch"), recursive=True):
        seen += 1
        for hit in scan(f):
            print("REACH", f, hit)
print(f"scanned {seen} .kicad_sch files")

STUB = re.compile(r"stub_length['\"]?\s*[:=]\s*([-+0-9.eE]+)")
tiny = 0
for root in RUNS:
    for f in glob.glob(str(root / "**" / "*.jsonl"), recursive=True):
        for line in open(f, encoding="utf-8"):
            for m in STUB.finditer(line):
                try:
                    v = float(m.group(1))
                except ValueError:
                    continue
                if 0 < v < 0.00005:
                    tiny += 1
                    r = json.loads(line)
                    print("STUB", f, r.get("case"), r.get("variant"), r.get("status"), v)
print(f"records with 0 < stub_length < 0.00005: {tiny}")
