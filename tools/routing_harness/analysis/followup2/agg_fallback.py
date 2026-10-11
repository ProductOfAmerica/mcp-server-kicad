"""Follow-up 2: recovery after a connect_pins refusal, across every batch that measured it.

A. For every connect_pins call design H refused: what a caller tried next, on a fresh copy of the
   same before-state, judged by judge3 (cli measured, gui emulated):
     wptn   H.wire_pins_to_net([p1, p2], <fresh shared name>)
     label  the repo's add_label(<fresh shared name>) at every drawn copy of both pin ends
   'recovers' = status OK, wrong False in both readings, both pins delivered in both readings.
B. E's suggested fallback: for every connect_pins call E refused, E.wire_pins_to_net([p1, p2],
   <fresh shared name>), split by whether E's refusal codes say both pins were free (only corridor
   codes: blocked) or not.
Sources (records of one run, --run; a follow-up 2 run is `run.py --designs M2,H,Hb,E --fu2`):
  demo     <run>/demo/records.jsonl           samples demo_connect, fallback, recov_c, e_split
  probeA   <run>/probeA/records.jsonl         connect rows carry fallback_wptn / fallback_label
                                              (written by suites/probeA/summarize.py: run it first)
  probeB   <run>/probeB/rec/*.jsonl           call records + fallback records (same run_id)
  probeC   <run>/probeC/parts/*.jsonl         main/seq records + fallback records (same slot)
probeB and probeC run fallbacks only for a refusal some other design did not make; probeA and
the demo sweep run them after every refusal. Coverage is reported per batch.
Run: uv run python tools/routing_harness/analysis/followup2/agg_fallback.py --run NAME > agg_fallback.out
Also writes <run>/analysis/agg_fallback.json. Needs the probeC coverage rerun (mk_fball_jobs.py) for
the refusals the main pass skipped; without it those are reported as 'no rerun record'.
"""

import argparse
import glob
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import readings  # noqa: E402

ap = argparse.ArgumentParser(description="Recovery after a connect_pins refusal, across the batches of one run.")
ap.add_argument("--run", default=hpaths.RUN,
                help="run name under work/runs, or a run directory path (default: ROUTING_HARNESS_RUN, else default)")
args = ap.parse_args()
R2 = hpaths.run_dir(args.run)  # the run directory (follow-up 2's records were in scratch rerun2)
if not R2.is_dir():
    sys.exit(f"no such run directory: {R2}")
for _src in (R2 / "demo" / "records.jsonl", R2 / "probeA" / "records.jsonl"):
    if not _src.is_file():
        sys.exit(f"run {R2.name} has no {_src.relative_to(R2).as_posix()}: run that batch first "
                 "(probeA's file is written by suites/probeA/summarize.py)")
print(f"run {R2.name}; readings: {readings.LABEL}")
FREE_CODES = {"blocked"}


def jl(p):
    return [json.loads(x) for x in open(p, encoding="utf-8") if x.strip()]


def outcome(r):
    if r is None:
        return "not run"
    st = r.get("status")
    if st == "REFUSED":
        return "refused"
    if st not in ("OK", "NOOP"):
        return f"status {st}"
    if readings.wrong(r):
        return "WRONG"
    if readings.unjudged(r):
        return "unjudged"
    if all(r.get(f"delivered_{rd}") for rd in readings.READINGS):
        return "recovers"
    return "clean, not delivered"


rows = []  # {batch, design, codes, wptn, label, id}

# demo
demo = jl(R2 / "demo" / "records.jsonl")
by = defaultdict(dict)
for r in demo:
    by[(r["group"], r["case"])].setdefault(r["sample"], []).append(r)
for (g, c), d in by.items():
    conn = {r["design"]: r for r in d.get("demo_connect", [])}
    fb = {r["design"]: r for r in d.get("fallback", [])}
    rc = (d.get("recov_c") or [None])[0]
    for des in ("H", "E"):
        r = conn.get(des)
        if r and r["status"] == "REFUSED":
            rows.append({"batch": "demo", "design": des, "codes": r["codes"], "id": f"{g}|{c}",
                         "wptn": outcome(fb.get(des)),
                         "label": outcome(rc) if (rc and des in (rc.get("recovers_for") or [])) else "not run"})

# probeA
for r in jl(R2 / "probeA" / "records.jsonl"):
    if r.get("tool") == "connect_pins" and r.get("status") == "REFUSED" and r.get("primary") \
            and r.get("design") in ("H", "E"):
        fw, fl = r.get("fallback_wptn"), r.get("fallback_label")
        conv = {"clean_delivered": "recovers", "refused": "refused", "wrong": "WRONG",
                "error": "status ERROR", "unjudged": "unjudged",
                "clean_not_delivered": "clean, not delivered"}
        rows.append({"batch": "probeA", "design": r["design"], "codes": r.get("codes") or [],
                     "id": f"{r.get('group')}|{r.get('case')}|{r.get('variant')}|s{r.get('step')}",
                     "wptn": conv.get((fw or {}).get("outcome"), "not run") if fw else "not run",
                     "label": conv.get((fl or {}).get("outcome"), "not run") if fl else "not run"})

# probeB
calls, fbs = [], defaultdict(list)
for f in glob.glob(str(R2 / "probeB" / "rec" / "*.jsonl")):
    for r in jl(f):
        if r.get("record_type") == "call":
            calls.append(r)
        elif r.get("record_type") == "fallback":
            fbs[(r.get("run_id"), r.get("for_design"))].append(r)
seen = set()
for r in calls:
    if r.get("tool") != "connect_pins" or r.get("status") != "REFUSED" or r.get("design") not in ("H", "E"):
        continue
    k = (r.get("run_id"), r["design"])
    if k in seen:
        continue
    seen.add(k)
    fl = [x for x in fbs.get(k, []) if x.get("fallback") == "fallback_label"]
    fw = [x for x in fbs.get(k, []) if x.get("fallback") == "fallback_wptn"]
    rows.append({"batch": "probeB", "design": r["design"], "codes": r.get("codes") or [],
                 "id": r.get("run_id"), "wptn": outcome(fw[0]) if fw else "not run",
                 "label": outcome(fl[0]) if fl else "not run"})

# probeC
pc = []
for f in glob.glob(str(R2 / "probeC" / "parts" / "*.jsonl")):
    pc += jl(f)
slot_fb = defaultdict(dict)
for r in pc:
    if r.get("kind") in ("fallback_label", "fallback_wptn") and r.get("fallback_of") == "connect_pins":
        slot_fb[(r.get("part"), r.get("variant"), r.get("step"), r.get("design"))][r["kind"]] = r
# probeC coverage rerun (RLIB_FB_ALL=1 into parts_fball/, 2026-10-03): fallbacks for refusals the
# main pass skipped. Used only where the rerun's call saw the same UUID-normalised before-state
# (in_sig_norm) and was refused again with the same codes.
fb_calls, fb_fbs = {}, defaultdict(dict)
for f in glob.glob(str(R2 / "probeC" / "parts_fball" / "*.jsonl")):
    for r in jl(f):
        k = (r.get("part"), r.get("variant"), r.get("step"), r.get("design"))
        if r.get("kind") in ("main", "seq") and r.get("tool") == "connect_pins":
            fb_calls[k] = r
        elif r.get("kind") in ("fallback_label", "fallback_wptn") and r.get("fallback_of") == "connect_pins":
            fb_fbs[k][r["kind"]] = r
cover = Counter()
for r in pc:
    if r.get("kind") in ("main", "seq") and r.get("tool") == "connect_pins" \
            and r.get("status") == "REFUSED" and r.get("design") in ("H", "E"):
        k = (r.get("part"), r.get("variant"), r.get("step"), r["design"])
        s = slot_fb.get(k, {})
        src = "main"
        if not s:
            rc = fb_calls.get(k)
            if rc is None:
                src = "no rerun record"
            elif rc.get("in_sig_norm") != r.get("in_sig_norm") or rc.get("status") != "REFUSED" \
                    or rc.get("codes") != r.get("codes"):
                src = "rerun state or outcome differs"
            else:
                s, src = fb_fbs.get(k, {}), "coverage rerun"
        cover[(r["design"], src)] += 1
        rows.append({"batch": "probeC", "design": r["design"], "codes": r.get("codes") or [],
                     "id": f"{r.get('part')}|{r.get('case')}|{r.get('variant')}|s{r.get('step')}",
                     "wptn": outcome(s.get("fallback_wptn")) if s.get("fallback_wptn") else "not run",
                     "label": outcome(s.get("fallback_label")) if s.get("fallback_label") else "not run"})
print("probeC fallback source per refusal:", dict(cover))

out = {"A_H_connect_refusals": {}, "B_E_connect_refusals": {}}
print("A. After an H connect_pins refusal (fresh copy of the before-state each)")
for batch in ("demo", "probeA", "probeB", "probeC", "ALL"):
    hr = [x for x in rows if x["design"] == "H" and (batch == "ALL" or x["batch"] == batch)]
    if not hr:
        continue
    per = defaultdict(lambda: {"n": 0, "wptn": Counter(), "label": Counter()})
    for x in hr:
        key = "+".join(sorted(set(x["codes"]))) or "(none)"
        per[key]["n"] += 1
        per[key]["wptn"][x["wptn"]] += 1
        per[key]["label"][x["label"]] += 1
    out["A_H_connect_refusals"][batch] = {k: {"n": v["n"], "wptn": dict(v["wptn"]),
                                              "label": dict(v["label"])} for k, v in per.items()}
    print(f"  {batch}: {len(hr)} refusals")
    for k, v in sorted(per.items(), key=lambda t: -t[1]["n"]):
        print(f"    [{k}] n={v['n']}  H wptn shared name: {dict(v['wptn'])}  repo label shared name:"
              f" {dict(v['label'])}")
print("\nB. After an E connect_pins refusal: E.wire_pins_to_net([p1, p2], shared fresh name)")
for batch in ("demo", "probeA", "probeB", "probeC", "ALL"):
    er = [x for x in rows if x["design"] == "E" and (batch == "ALL" or x["batch"] == batch)]
    if not er:
        continue
    free = [x for x in er if set(x["codes"]) and set(x["codes"]) <= FREE_CODES]
    notfree = [x for x in er if x not in free]
    out["B_E_connect_refusals"][batch] = {
        "n": len(er), "both_free": len(free), "both_free_wptn": dict(Counter(x["wptn"] for x in free)),
        "not_free_wptn": dict(Counter(x["wptn"] for x in notfree)),
        "both_free_items": [(x["id"], x["codes"], x["wptn"]) for x in free]}
    print(f"  {batch}: {len(er)} refusals; both pins free (codes only {sorted(FREE_CODES)}):"
          f" {len(free)} -> {dict(Counter(x['wptn'] for x in free))}; not free: {len(notfree)} ->"
          f" {dict(Counter(x['wptn'] for x in notfree))}")
    for x in free:
        if x["wptn"] != "recovers":
            print(f"      counterexample: {x['id']} codes {x['codes']} -> {x['wptn']}")
out["readings"] = readings.LABEL
(R2 / "analysis").mkdir(parents=True, exist_ok=True)
(R2 / "analysis" / "agg_fallback.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
