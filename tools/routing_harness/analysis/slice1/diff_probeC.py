"""Slice 1 gate: probeC's refusal rate rose from Hb2's 30.0% to X's 32.1%. Pair every probeC
main/seq call of design X (the --x run) with Hb2's (the --base run) by part, case, variant, step and
tool, and print each pair whose status differs, with both heads, so each rise gets a cause.
Run (RUN is a run name under work/runs or a path):
    uv run python tools/routing_harness/analysis/slice1/diff_probeC.py --x RUN --base RUN > diff_probeC.out
"""

import argparse
import glob
import json
import sys
from collections import Counter
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)

TOOLS = ("wire_pins_to_net", "connect_pins", "auto_place_decoupling_cap")


def load(root, design):
    out = {}
    dup = Counter()
    for f in glob.glob(str(root / "probeC" / "parts" / "*.jsonl")):
        part = Path(f).stem
        for line in open(f, encoding="utf-8"):
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("kind") not in ("main", "seq") or r.get("tool") not in TOOLS or r.get("design") != design:
                continue
            k = (part, r.get("case"), r.get("variant"), r.get("step"), r.get("tool"))
            dup[k] += 1
            out[(*k, dup[k])] = r
    return out


_ap = argparse.ArgumentParser(description="Pair probeC calls of design X with Hb2's (slice 1 gate).")
_ap.add_argument("--x", required=True, help="run holding design X's probeC parts")
_ap.add_argument("--base", required=True, help="run holding design Hb2's probeC parts")
_a = _ap.parse_args()
for _r in (_a.x, _a.base):
    if not hpaths.run_dir(_r).is_dir():
        sys.exit(f"run directory not found: {hpaths.run_dir(_r)}")
x, h = load(hpaths.run_dir(_a.x), "X"), load(hpaths.run_dir(_a.base), "Hb2")
print(f"X {len(x)} calls, Hb2 {len(h)} calls; only in X {len(set(x) - set(h))}, only in Hb2 {len(set(h) - set(x))}")
for k in sorted(set(x) - set(h))[:20]:
    print("  only X:", k)
for k in sorted(set(h) - set(x))[:20]:
    print("  only Hb2:", k)
moves = Counter()
for k in sorted(set(x) & set(h)):
    a, b = h[k], x[k]
    if a.get("status") != b.get("status"):
        moves[(k[4], a.get("status"), b.get("status"))] += 1
        print(f"\n{k[0]} {k[1]} {k[2]} step={k[3]} {k[4]}: Hb2 {a.get('status')} -> X {b.get('status')}")
        print("  kw:", str(b.get("kw"))[:300])
        print("  Hb2:", (a.get("msg") or a.get("msg_head") or "").replace("\n", " | ")[:400])
        print("  X  :", (b.get("msg") or b.get("msg_head") or "").replace("\n", " | ")[:700])
print()
for m, n in sorted(moves.items()):
    print(n, m)
