"""Follow-up 3: every call where Hb's H-C1 fired ([H:dup_ref], follow-up 2 records: the old run), and
what Hb2 (new H-C1, follow-up 3 records: the new run) did at the same slot, judged by judge3 (the
cli reading is measured; the gui reading is not available in this repository, so outcomes cover
the readings that were taken, see core/readings.py). Also every [H:dup_ref] Hb2 still makes, with
its stated reason.
Slots: probeA (group, variant, step), probeB run_id, probeC (part, variant, kind, step),
layouts (sample, group, policy, case, call_index), demo (sample, group, case).
Run: uv run python tools/routing_harness/analysis/dupref/lift.py --old RUN:Hb --new RUN:Hb2
         > <new run>/analysis/lift.out  (also writes <new run>/analysis/lift.json)
--old and --new take RUN:DESIGN; RUN is a run name or a directory (hpaths.run_dir). Without :DESIGN
they mean Hb and Hb2. Without the options both mean the current run (ROUTING_HARNESS_RUN, default
"default"), which then holds both designs.
"""
import argparse
import glob
import json
import re
from collections import Counter
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)

import readings  # noqa: E402


def _spec(text, design):
    """RUN[:DESIGN] to (run directory, design); the design defaults to the given one, the run to
    the current one (an empty RUN, as in :Hb)."""
    run, sep, d = text.rpartition(":")
    if not sep or not d.isidentifier():
        run, d = text, design
    root = hpaths.run_dir(run or hpaths.RUN)
    if not root.is_dir():
        sys.exit(f"run directory not found: {root}")
    return root, d


_ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
_ap.add_argument("--old", default=f"{hpaths.RUN}:Hb", metavar="RUN:DESIGN",
                 help="records of the old design (default: the current run, design Hb)")
_ap.add_argument("--new", default=f"{hpaths.RUN}:Hb2", metavar="RUN:DESIGN",
                 help="records of the new design (default: the current run, design Hb2)")
_args = _ap.parse_args()
OLD_ROOT, OLD_D = _spec(_args.old, "Hb")
NEW_ROOT, NEW_D = _spec(_args.new, "Hb2")
OUT = NEW_ROOT / "analysis"
OUT.mkdir(parents=True, exist_ok=True)
TOOLS = ("wire_pins_to_net", "connect_pins", "auto_place_decoupling_cap")


def jl(p):
    return [json.loads(x) for x in open(p, encoding="utf-8") if x.strip()]


def load(batch, root, D):
    """{slot: record} of design D's calls in one batch of one pass."""
    out = {}
    if batch == "probeA":
        for f in glob.glob(str(root / "probeA" / "records_*.jsonl")):
            for r in jl(f):
                if r.get("primary") and r.get("design") == D:
                    out[(r["group"], r["variant"], r.get("step"))] = r
    elif batch == "probeB":
        for f in glob.glob(str(root / "probeB" / "rec" / "*.jsonl")):
            for r in jl(f):
                if r.get("record_type") == "call" and r.get("design") == D:
                    out.setdefault(r["run_id"], r)
    elif batch == "probeC":
        for f in glob.glob(str(root / "probeC" / "parts" / "*.jsonl")):
            for r in jl(f):
                if r.get("kind") in ("main", "seq") and r.get("tool") in TOOLS and r.get("design") == D:
                    out[(r.get("part"), r.get("variant"), r.get("kind"), r.get("step"))] = r
    elif batch == "layouts":
        for r in jl(root / "layouts_stock" / "records.jsonl"):
            if r.get("design") == D:
                out[(r["sample"], r["group"], r.get("policy"), r["case"], r.get("call_index"))] = r
    elif batch == "demo":
        for r in jl(root / "demo" / "records.jsonl"):
            if r.get("design") == D and r["sample"] in ("demo_wptn", "demo_connect", "fallback"):
                out[(r["sample"], r["group"], r["case"])] = r
    elif batch == "llm":
        for f in glob.glob(str(root / "llm-workflow" / "parts" / "*.jsonl")):
            for r in jl(f):
                if r.get("design") == D:
                    out[(r.get("part"), r.get("case"), r.get("variant"), r.get("tool"), r.get("kw"),
                         r.get("attempt"))] = r
    return out


def slot_of(k):
    return [k] if isinstance(k, str) else [str(x) for x in k]


def outcome(r):
    if r is None:
        return "no record"
    st = r.get("status")
    if st == "REFUSED":
        codes = r.get("codes") or []
        return "still dup_ref" if "dup_ref" in codes else "refused " + "+".join(codes)
    if r.get("cli_wrong") or r.get("gui_wrong"):
        return f"{st} WRONG"
    if readings.unjudged(r):
        return f"{st} unjudged"
    if st == "NOOP":
        return "NOOP, correct" if not r.get("false_noop") else "NOOP, FALSE"
    ok = all(r.get(f"delivered_{rd}") for rd in readings.READINGS)
    return "OK clean, delivered" if ok else "OK clean, not delivered"


def reason(r):
    m = re.search(r"\[H:dup_ref\][^:]*: reference (\S+) is carried by .*?\): (.*?); KiCad joins",
                  (r.get("msg") or r.get("msg_head") or "").replace("\n", " "))
    return f"{m.group(1)}: {m.group(2)}" if m else (r.get("msg_head") or "")[:160]


res = {"per_batch": {}, "items": [], "hb2_dup_ref": []}
print(f"old: {OLD_ROOT.name} {OLD_D}; new: {NEW_ROOT.name} {NEW_D}; {readings.LABEL}")
for batch in ("probeA", "probeB", "probeC", "llm", "layouts", "demo"):
    old = load(batch, OLD_ROOT, OLD_D)
    new = load(batch, NEW_ROOT, NEW_D)
    fired = {k: r for k, r in old.items() if r.get("status") == "REFUSED" and "dup_ref" in (r.get("codes") or [])}
    cnt = Counter()
    for k, r in fired.items():
        n = new.get(k)
        o = outcome(n)
        cnt[o] += 1
        res["items"].append({"batch": batch, "slot": slot_of(k), "old_codes": r.get("codes"),
                             "case": r.get("case"), "hb2": o, "hb2_codes": (n or {}).get("codes"),
                             "hb2_msg": ((n or {}).get("msg_head") or "")[:200]})
    stilldup = [(k, r) for k, r in new.items() if r.get("status") == "REFUSED" and "dup_ref" in (r.get("codes") or [])]
    for k, r in stilldup:
        res["hb2_dup_ref"].append({"batch": batch, "slot": slot_of(k), "case": r.get("case"),
                                   "reason": reason(r), "was_dup_ref_in_Hb": k in fired})
    res["per_batch"][batch] = {"hb_dup_ref_calls": len(fired), "hb2_outcomes": dict(cnt),
                               "hb2_dup_ref_calls": len(stilldup),
                               "hb2_records": len(new), "hb_records": len(old)}
    print(f"{batch}: Hb dup_ref calls {len(fired)} -> Hb2 {dict(cnt)}; Hb2 dup_ref calls {len(stilldup)}"
          f" (records Hb {len(old)}, Hb2 {len(new)})")
print("\nHb2 [H:dup_ref] refusals and their reasons:")
for x in Counter((d["batch"], d["case"], d["reason"]) for d in res["hb2_dup_ref"]).items():
    print("  ", x[1], "x", x[0])
print("\nlifted calls that wrote or no-op'd:")
for it in res["items"]:
    if it["hb2"].startswith(("OK", "NOOP")):
        print("  ", it["batch"], it["case"], "|", " | ".join(it["slot"])[:120], "->", it["hb2"])
tot = Counter(it["hb2"] for it in res["items"])
print("\nTOTAL Hb dup_ref calls:", len(res["items"]), "->", dict(tot))
(OUT / "lift.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
