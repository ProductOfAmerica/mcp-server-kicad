"""perf-03 / perf-04 probe calls on EXISTING stock pins of the repathed stock sheets
(fixtures/probe/<name>.kicad_sch, byte-identical to probe-perf/sheets/), on M2 and K through rlib.

  perf_case_wptn_stock.py    wire_pins_to_net([pin], 'PT_STOCK') for every prepare.json pin
                             (unnamed + free) on ampli_ht, tinytapeout-demo, fpga-hp-banks, one
                             call per pin on a fresh copy. perf_case_wptn_stock_detail.py's two
                             calls (ampli_ht C201:2, tinytapeout-demo D1:2) are among these.
  perf_case_overlap_merge.py connect_pins on every prepare.json pair of ampli_ht and
                             tinytapeout-demo, one call per pair on a fresh copy.
                             perf_case_c201_split.py makes exactly the same calls (perf-04).
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib as R  # noqa: E402

import json  # noqa: E402

import pfix as F  # noqa: E402

PART = sys.argv[1] if len(sys.argv) > 1 else "W"
R.begin("perf", __file__, tag=f"perf_probe_stock_{PART}")
PINS = F.load_pins()
DUPS = []
DETAIL = {("ampli_ht", "C201", "2"), ("tinytapeout-demo", "D1", "2")}

if PART == "W":
    for name in ("ampli_ht", "tinytapeout-demo", "fpga-hp-banks"):
        p = F.fx(f"probe/{name}.kicad_sch", f"{name}.probe")
        prep = PINS["prepare"][name]
        for q in prep["unnamed"] + prep["free"]:
            v = f"perf_case_wptn_stock:{name}:{q['ref']}:{q['pin']}"
            note = f"{q['net']} ({q['net_size']} pins)"
            if (name, q["ref"], q["pin"]) in DETAIL:
                note += "; perf_case_wptn_stock_detail.py inspects this call"
                DUPS.append({"variant": f"perf_case_wptn_stock_detail:{name}:{q['ref']}:{q['pin']}",
                             "same_as": v})
            R.run_case("perf-03", v, "wire_pins_to_net", p,
                       {"pins": [{"reference": q["ref"], "pin": q["pin"]}], "label_text": "PT_STOCK"},
                       script="probe-perf/perf_case_wptn_stock.py", also=["perf-04"], note=note)

elif PART == "C":
    for name in ("ampli_ht", "tinytapeout-demo"):
        p = F.fx(f"probe/{name}.kicad_sch", f"{name}.probe")
        for q in PINS["prepare"][name]["pairs"]:
            a, b = (q["a"]["ref"], q["a"]["pin"]), (q["b"]["ref"], q["b"]["pin"])
            v = f"perf_case_overlap_merge:{name}:{a[0]}:{a[1]}->{b[0]}:{b[1]}"
            R.run_case("perf-03", v, "connect_pins", p,
                       {"ref1": a[0], "pin1": a[1], "ref2": b[0], "pin2": b[1]},
                       script="probe-perf/perf_case_overlap_merge.py", also=["perf-04"],
                       note=f"dist {q['dist_mm']} mm; perf_case_c201_split.py makes the same call")
            DUPS.append({"variant": f"perf_case_c201_split:{name}:{a[0]}:{a[1]}->{b[0]}:{b[1]}",
                         "same_as": v})

(F.OUTD / f"dups_probe_stock_{PART}.json").write_text(json.dumps(DUPS, indent=1), encoding="utf-8")
R.end()
