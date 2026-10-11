"""Slice 1 gate, targeted rerun for 708995a: which gate inputs hold a symbol that draws one pad
number under two or more names? Only there can resolution by name change (the old code kept the
first name of each number). Scans the lib_symbols of every .kicad_sch on disk (fixtures, kept
before/after copies, KiCad's demos), every stock .kicad_sym, and greps the harness builders for
the stock symbols found.
Run (RUN is a run name under work/runs or a path; --run repeats; default: the current run; the
harness builders are the scripts under suites/):
    uv run python tools/routing_harness/analysis/slice1/scan_multiname.py --run RUN > scan_multiname.out
"""

import argparse
import glob
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
from mcp_server_kicad import _cst  # noqa: E402

_ap = argparse.ArgumentParser(description="Which gate inputs hold a symbol drawing one pad number under 2+ names? (slice 1 gate)")
_ap.add_argument("--run", action="append",
                 help="run to scan: a name under work/runs or a path; repeatable (default: the current run)")
RUNS = [hpaths.run_dir(r) for r in (_ap.parse_args().run or [hpaths.RUN])]
for _r in RUNS:
    if not _r.is_dir():
        sys.exit(f"run directory not found: {_r}")
KICAD_DEMOS = Path(hpaths.KICAD_DEMOS)
KICAD_SYMBOLS = Path(hpaths.KICAD_SYMBOLS)


def multi(sym) -> dict:
    names: dict[str, set] = {}
    for sub in sym.find_all("symbol"):
        for pin in sub.find_all("pin"):
            num, name = pin.find("number"), pin.find("name")
            if num is not None and name is not None:
                names.setdefault(num.atoms[1].text, set()).add(name.atoms[1].text)
    return {n: sorted(v) for n, v in names.items() if len(v) > 1}


def symbols(path: Path, in_sch: bool):
    data = path.read_bytes()
    if data.count(b"(pin ") < 2:
        return
    root = _cst.parse(data).lists[0]
    holder = root.find("lib_symbols") if in_sch else root
    for s in holder.find_all("symbol") if holder is not None else []:
        m = multi(s)
        if m:
            yield s.atoms[1].text, m


found_sch = set()
for root in (*RUNS, KICAD_DEMOS):
    for f in glob.glob(str(root / "**" / "*.kicad_sch"), recursive=True):
        p = Path(f)
        if not p.is_file():
            continue
        for name, m in symbols(p, True):
            found_sch.add((name, str(sorted(m.items()))[:120]))
            print("SCH", f, name, str(sorted(m.items()))[:160])
print("distinct symbols in .kicad_sch files:", sorted(found_sch))

stock = {}
for f in glob.glob(str(KICAD_SYMBOLS / "*.kicad_sym")):
    lib = Path(f).stem
    for name, m in symbols(Path(f), False):
        stock[f"{lib}:{name}"] = m
print(f"stock symbols drawing a pad number under 2+ names: {len(stock)}")
for k in sorted(stock):
    print("  STOCK", k, str(sorted(stock[k].items()))[:140])

scripts = [Path(f) for f in glob.glob(str(hpaths.SUITES / "**" / "*.py"), recursive=True)]
for k in sorted(stock):
    lib, name = k.split(":", 1)
    pat = re.compile(re.escape(f"{lib}:{name}") + r"\b")
    hits = [str(s) for s in scripts if pat.search(s.read_text(encoding="utf-8", errors="replace"))]
    if hits:
        print("BUILDER", k, hits)
