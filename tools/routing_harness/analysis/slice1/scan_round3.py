"""Slice 1 gate, targeted rerun for the second review's fixes (0325056 library keys, dc9de01 net
names): which gate inputs can they reach?
- a placed symbol whose library name lib_symbols holds twice, or a library name with {slash};
- a KiCad escape token ({slash}, {dblquote}, ...) in a label, sheet pin, Value or pin name;
- a label_text argument holding a brace.
Scans every .kicad_sch on disk under the given runs and KiCad's demos, every harness builder
script (suites/**/*.py) for the tokens, and every record of the given runs for brace-holding
label_text arguments.
Run (RUN is a run name under work/runs or a path; --run repeats; default: the current run):
    uv run python tools/routing_harness/analysis/slice1/scan_round3.py --run RUN > scan_round3.out
"""

import argparse
import glob
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)

_ap = argparse.ArgumentParser(description="Which gate inputs can the second review's fixes reach? (slice 1 gate)")
_ap.add_argument("--run", action="append",
                 help="run to scan: a name under work/runs or a path; repeatable (default: the current run)")
RUNS = [hpaths.run_dir(r) for r in (_ap.parse_args().run or [hpaths.RUN])]
for _r in RUNS:
    if not _r.is_dir():
        sys.exit(f"run directory not found: {_r}")
DEMOS = Path(hpaths.KICAD_DEMOS)
TOKENS = ["dblquote", "quote", "lt", "gt", "backslash", "slash", "bar", "comma", "colon",
          "space", "dollar", "tab", "return", "brace"]
TOK = re.compile(rb"\{(" + b"|".join(t.encode() for t in TOKENS) + rb")\}")
SYM = re.compile(rb'\(symbol\s+"([^"]*)"')

sch_hits = Counter()
for root in (*RUNS, DEMOS):
    for f in glob.glob(str(root / "**" / "*.kicad_sch"), recursive=True):
        p = Path(f)
        if not p.is_file():
            continue
        data = p.read_bytes()
        toks = TOK.findall(data)
        lib = data.find(b"(lib_symbols")
        names = []
        if lib >= 0:
            # top-level lib_symbols entries: (symbol "NAME" at one tab depth below lib_symbols
            for m in SYM.finditer(data, lib):
                name = m.group(1)
                if not re.search(rb"_\d+_\d+$", name):
                    names.append(name)
                if data.startswith(b"\t(symbol\n", m.end()) or m.start() > lib + 10_000_000:
                    break
        dup = [n for n, k in Counter(names).items() if k > 1]
        if toks or dup:
            sch_hits[(str(p), tuple(sorted(set(t.decode() for t in toks))), tuple(d.decode() for d in dup))] += 1
for (f, toks, dup), _n in sorted(sch_hits.items()):
    print("SCH", f, "tokens", toks, "dup lib names", dup)
print(f"sheets with escape tokens or repeated lib names: {len(sch_hits)}")

for f in glob.glob(str(hpaths.SUITES / "**" / "*.py"), recursive=True):
    text = Path(f).read_bytes()
    if TOK.search(text):
        print("SCRIPT", f, sorted(set(t.decode() for t in TOK.findall(text))))

brace = 0
for root in RUNS:
    for f in glob.glob(str(root / "**" / "*.jsonl"), recursive=True):
        for line in open(f, encoding="utf-8"):
            r = json.loads(line)
            kw = r.get("kw") or r.get("args") or r.get("call") or ""
            text = json.dumps(kw) if not isinstance(kw, str) else kw
            m = re.search(r"label_text['\"]?\s*[:=]\s*['\"]([^'\"]*)['\"]", text)
            if m and "{" in m.group(1):
                brace += 1
                print("CALL", Path(f).name, r.get("case"), r.get("variant"), repr(m.group(1)), r.get("status"))
print(f"gate calls with a brace in label_text: {brace}")
