"""Aggregate run_generated(.classified).json: per impl/policy and per knob.

Metrics (first attempts = calls the plan issues, not adaptive retries or fallbacks):
  calls / OK / NOOP / REFUSED and refusal rate; pins named in refused wire calls, split into
  pins the tool itself refused and collateral pins (all-or-nothing); refusal verdicts from
  classify.py; problems by kind over ALL calls (incl. fallbacks and retries); final plan
  completion (planned pins not on their plan net) and runs ending with a short; replay.

usage: analyze_generated.py [path of a run_generated.json] (default: the one in the run's lw13
output directory, hpaths.suite_out("lw13"))
Run: uv run python tools/routing_harness/analysis/lw13/analyze_generated.py
"""

import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__, suites=("layouts_stock",))
from summarize import kinds  # noqa: E402

F = str(hpaths.suite_out("lw13") / "run_generated.json")


def main():
    f = sys.argv[1] if len(sys.argv) > 1 else F
    cf = f.replace(".json", ".classified.json")
    runs = json.load(open(cf if os.path.exists(cf) else f, encoding="utf-8"))
    agg = defaultdict(Counter)
    knob = defaultdict(Counter)
    examples = defaultdict(list)
    for r in runs:
        key = (r["impl"], r["policy"])
        a = agg[key]
        a["runs"] += 1
        first = [x for x in r["records"] if "[" not in x["tag"]]
        for x in first:
            a["calls"] += 1
            a[x["status"]] += 1
            a[f"{x['kind']}_calls"] += 1
            if x["status"] == "REFUSED":
                a[f"{x['kind']}_refused"] += 1
        for x in r["records"]:
            for c in x.get("classified", []):
                v = c["verdict"]
                if v == "collateral":
                    n = int(c["why"].split(" ")[0])
                    a["collateral_pins"] += n
                else:
                    a[f"verdict:{v}"] += 1
                    a[f"verdict:{v}:{c['what'].split(' ', 1)[-1]}"] += 1
                    if len(examples[(key, v)]) < 6:
                        examples[(key, v)].append(f"{r['design']} {x['tag']}: {c['what']} -> {c['why']}")
            for k in kinds(x["problems"]):
                a[f"problem:{k}"] += 1
                if len(examples[(key, k)]) < 4:
                    examples[(key, k)].append(f"{r['design']} {x['tag']}: {' | '.join(x['problems'])[:300]}")
            for h in x.get("hazards", []):
                a[f"hazard:{h.split(':')[0]}"] += 1
        fin = r["final"]
        a["planned_pins"] += fin.get("planned_pins", 0)
        a["pins_missing"] += fin.get("pins_not_on_plan_net", 0)
        a["runs_with_short"] += bool(fin.get("shorts"))
        a["runs_complete"] += fin.get("pins_not_on_plan_net", 1) == 0 and not fin.get("shorts")
        for x in r.get("replay", []):
            a["replay_calls"] += 1
            a[f"replay_{x['status']}"] += 1
            a["replay_changed"] += x["changed"]
            for k in kinds(x["problems"]):
                a[f"replay_problem:{k}"] += 1
        if r["impl"] in ("P", "M") and r["policy"] in ("skill",):
            ks = r["knobs"]
            for kn in ("ic", "naming", "decap_arr", "decap_pitch", "cap_rot", "r_rot", "led_rot",
                       "conn_rot", "sensor"):
                kk = (r["impl"], kn, str(ks[kn]))
                knob[kk]["runs"] += 1
                knob[kk]["calls"] += len(first)
                knob[kk]["refused"] += sum(1 for x in first if x["status"] == "REFUSED")
                knob[kk]["missing"] += fin.get("pins_not_on_plan_net", 0)
                knob[kk]["planned"] += fin.get("planned_pins", 0)
    for key in sorted(agg):
        a = agg[key]
        print(f"\n== impl={key[0]} policy={key[1]}: {a['runs']} runs, {a['calls']} first-attempt calls")
        print(f"   OK {a['OK']}  NOOP {a['NOOP']}  REFUSED {a['REFUSED']} "
              f"({100 * a['REFUSED'] / max(a['calls'], 1):.1f}%)  ERROR {a['ERROR']} | wire refused "
              f"{a['wire_refused']}/{a['wire_calls']}, connect refused {a['connect_refused']}/{a['connect_calls']}")
        vs = {k[8:]: v for k, v in a.items() if k.startswith("verdict:")}
        print(f"   refusal verdicts: {vs}; collateral pins (all-or-nothing): {a['collateral_pins']}")
        ps = {k[8:]: v for k, v in a.items() if k.startswith("problem:")}
        print(f"   problems over all calls: {ps}")
        hz = {k[7:]: v for k, v in a.items() if k.startswith("hazard:")}
        if hz:
            print(f"   hazards created: {hz}")
        print(f"   final: {a['pins_missing']}/{a['planned_pins']} planned pins not on their plan net "
              f"({100 * a['pins_missing'] / max(a['planned_pins'], 1):.1f}%); runs complete and short-free "
              f"{a['runs_complete']}/{a['runs']}; runs ending with a short {a['runs_with_short']}")
        if a["replay_calls"]:
            rp = {k[7:]: v for k, v in a.items() if k.startswith("replay_") and k != "replay_calls"}
            print(f"   replay: {a['replay_calls']} calls {rp}")
        for (k2, v), ex in examples.items():
            if k2 == key:
                for e in ex[:3]:
                    print(f"      e.g. [{v}] {e}")
    print("\n== P and M (skill policy) by knob: runs, first-attempt refusal rate, planned pins missing")
    for kk in sorted(knob):
        c = knob[kk]
        print(f"   {kk[0]} {kk[1]:12} {kk[2]:10} runs {c['runs']:3} refused {c['refused']:3}/{c['calls']:3} "
              f"({100 * c['refused'] / max(c['calls'], 1):4.1f}%)  missing {c['missing']:3}/{c['planned']:4}")


if __name__ == "__main__":
    main()
