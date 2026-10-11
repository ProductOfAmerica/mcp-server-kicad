"""Slice 1 gate: pair every call of design X (in the given run) with design Hb2's call on the
same input (the --base run), batch by batch, and print each pair whose outcome differs, with both
messages, so every change in refusal rate gets a cause. Sequences diverge once an outcome
differs, so a later step's pair may compare different file states; the first change in a sequence
is the one to read.
usage (RUN is a run name under work/runs or a path; batches: probeA probeB probeC llm layouts demo, default all):
    uv run python tools/routing_harness/analysis/slice1/diff_all.py <run of X> [batch ...] --base RUN > diff_all.out
The designs compared default to X (the run of X) and Hb2 (the --base run), as in the pressure test;
--design and --base-design name others, so `--base-design X` compares two runs of X call for call
(the --base run first in each pair).
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


def jl(pattern):
    for f in sorted(glob.glob(str(pattern))):
        for line in open(f, encoding="utf-8"):
            if line.strip():
                yield Path(f).stem, json.loads(line)


def keyed(rows, key):
    out, seen = {}, Counter()
    for part, r in rows:
        k = key(part, r)
        seen[k] += 1
        out[(*k, seen[k])] = r
    return out


BATCHES = {
    "probeA": ("probeA/records_*.jsonl", lambda r: r.get("primary"),
               lambda part, r: (part, r.get("group"), r.get("case"), r.get("variant"), r.get("step"), r.get("tool"))),
    "probeB": ("probeB/rec/*.jsonl", lambda r: r.get("record_type") == "call",
               lambda part, r: (r.get("run_id"),)),
    "probeC": ("probeC/parts/*.jsonl", lambda r: r.get("kind") in ("main", "seq") and r.get("tool") in TOOLS,
               lambda part, r: (part, r.get("case"), r.get("variant"), r.get("step"), r.get("tool"))),
    "llm": ("llm-workflow/parts/*.jsonl", lambda r: True,
            lambda part, r: (part, r.get("scenario"), r.get("variant"), r.get("step"), r.get("attempt"), r.get("tool"))),
    "layouts": ("layouts_stock/records.jsonl", lambda r: True,
                lambda part, r: (r.get("sample"), r.get("group"), r.get("policy"), r.get("call_index"), r.get("case"), r.get("variant"))),
    "demo": ("demo/records.jsonl", lambda r: True,
             lambda part, r: (r.get("sample"), r.get("group"), r.get("case"), r.get("tool"))),
}


def msg(r):
    return (r.get("msg") or r.get("msg_head") or "").replace("\n", " | ")


def main():
    ap = argparse.ArgumentParser(description="Pair every call of design X with Hb2's call on the same input.")
    ap.add_argument("xrun", help="run holding design X's records (a name under work/runs, or a path)")
    ap.add_argument("batch", nargs="*", help="batches to compare, default all: " + " ".join(BATCHES))
    ap.add_argument("--base", required=True, help="run holding the base records (design Hb2 by default)")
    ap.add_argument("--design", default="X", help="design taken from the run of X (default X)")
    ap.add_argument("--base-design", default="Hb2",
                    help="design taken from the --base run (default Hb2; X compares two runs of X)")
    args = ap.parse_args()
    d, bd = args.design, args.base_design
    xroot, hroot = hpaths.run_dir(args.xrun), hpaths.run_dir(args.base)
    for r in (xroot, hroot):
        if not r.is_dir():
            sys.exit(f"run directory not found: {r}")
    names = args.batch or list(BATCHES)
    unknown = [n for n in names if n not in BATCHES]
    if unknown:
        ap.error(f"unknown batch {unknown}; choose from {list(BATCHES)}")
    for name in names:
        pat, sel, key = BATCHES[name]
        x = keyed([(p, r) for p, r in jl(xroot / pat) if r.get("design") == d and sel(r)], key)
        h = keyed([(p, r) for p, r in jl(hroot / pat) if r.get("design") == bd and sel(r)], key)
        moves = Counter()
        print(f"######## {name}: {d} {len(x)} calls, {bd} {len(h)}; only {d} {len(set(x) - set(h))},"
              f" only {bd} {len(set(h) - set(x))}")
        for k in sorted(set(x) & set(h), key=str):
            a, b = h[k], x[k]
            if a.get("status") != b.get("status"):
                moves[(b.get("tool"), a.get("status"), b.get("status"))] += 1
                print(f"\n{k}: {bd} {a.get('status')} -> {d} {b.get('status')}")
                print("  call:", str(b.get("kw") or b.get("call") or b.get("args"))[:300])
                print(f"  {bd}:", msg(a)[:400])
                print(f"  {d:<3}:", msg(b)[:700])
        print(f"\n== {name} transitions")
        for m, n in sorted(moves.items(), key=str):
            print(" ", n, m)
        print()


if __name__ == "__main__":
    main()
