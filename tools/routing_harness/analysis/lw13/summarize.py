"""Per design x impl x policy: first-attempt calls, outcomes, problems, final plan completion,
replay idempotence. Reads <run>.classified.json when present (refusal verdicts; the classifier that
writes it is not part of this repository).
Run: uv run python tools/routing_harness/analysis/lw13/summarize.py <run_designs.json ...>"""

import json
import os
import sys
from collections import Counter
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)


def kinds(problems):
    out = []
    for p in problems:
        for k in ("UNINTENDED MERGE", "NAMED NETS MERGED", "SPLIT", "UNINTENDED RENAME", "PARTIAL",
                  "REQUEST NOT MET", "FALSE NO-OP", "WRONG PIN", "PINS VANISHED", "UNLOADABLE",
                  "FILE CHANGED", "PIN LEFT NET"):
            if p.startswith(k):
                out.append(k)
                break
        else:
            out.append(p.split(" ")[0])
    return out


def main():
    for f in sys.argv[1:]:
        cf = f.replace(".json", ".classified.json")
        runs = json.load(open(cf if os.path.exists(cf) else f, encoding="utf-8"))
        print(f"\n######## {f}")
        hdr = (f"{'design':13} {'impl':4} {'policy':8} {'calls':>5} {'OK':>3} {'NOOP':>4} {'REF':>3} "
               f"{'ref:just':>8} {'ref:legit':>9} {'collat':>6} {'probl':>5}  final(missing/planned, shorts)  replay")
        print(hdr)
        for r in runs:
            first = [x for x in r["records"] if "[" not in x["tag"]]
            st = Counter(x["status"] for x in first)
            verdicts = Counter()
            for x in r["records"]:
                for c in x.get("classified", []):
                    verdicts[c["verdict"]] += 1
            probs = Counter(k for x in r["records"] for k in kinds(x["problems"]))
            fin = r["final"]
            rp = Counter(x["status"] for x in r.get("replay", []))
            rprob = Counter(k for x in r.get("replay", []) for k in kinds(x["problems"]))
            rchanged = sum(1 for x in r.get("replay", []) if x["changed"])
            print(f"{r['design']:13} {r['impl']:4} {r['policy']:8} {len(first):5} {st['OK']:3} "
                  f"{st['NOOP']:4} {st['REFUSED']:3} {verdicts['justified']:8} "
                  f"{verdicts['LEGIT REFUSED']:9} {verdicts['collateral']:6} "
                  f"{sum(probs.values()):5}  {fin.get('pins_not_on_plan_net')}/{fin.get('planned_pins')}"
                  f" shorts={len(fin.get('shorts', []))}  "
                  f"replay {dict(rp)} changed={rchanged} {dict(rprob) if rprob else ''}")
            if probs:
                print(f"{'':30} problems: {dict(probs)}")
            for s in fin.get("shorts", []):
                print(f"{'':30} SHORT: {s}")
            if fin.get("incomplete"):
                print(f"{'':30} incomplete: {fin['incomplete']}")


if __name__ == "__main__":
    main()
