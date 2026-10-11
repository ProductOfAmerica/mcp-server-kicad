"""perf-05 / perf-07 probe connect_pins chains (perf_connect.py), on M2 and K through rlib.

Scenario "new": the augmented sheet (fixtures/probe/<name>.aug), pairs J9003:k -> J9002:k,
k = 1..10, one chain of 10 calls on one file. Scenario "stock": the repathed stock sheet
(fixtures/probe/<name>.kicad_sch, byte-identical to probe-perf/sheets), the nearest pairs of
prepare.json (equal to the original's), as one chain; fpga-power has none.
Each chain: min of 3 unjudged chains per design (pfix.time3_chain), then one judged chain per design
(rlib.run_sequence: each call judged against the state just before it).
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib as R  # noqa: E402

import pfix as F  # noqa: E402

R.begin("perf", __file__, tag="perf_probe_connect")
PINS = F.load_pins()

for name in ("ampli_ht", "tinytapeout-demo", "fpga-hp-banks", "fpga-power"):
    scen = {
        "new": (f"probe/{name}.aug.kicad_sch",
                [(("J9003", str(k)), ("J9002", str(k))) for k in range(1, 11)]),
        "stock": (f"probe/{name}.kicad_sch",
                  [((q["a"]["ref"], q["a"]["pin"]), (q["b"]["ref"], q["b"]["pin"]))
                   for q in PINS["prepare"][name]["pairs"]]),
    }
    for sname, (rel, pairs) in scen.items():
        if not pairs:
            print(f"# {name} [{sname}]: no pairs on this sheet (as in the original)", flush=True)
            continue
        p = F.fx(rel, f"{name}.{sname}")
        steps = [dict(tool="connect_pins", kw=dict(ref1=a[0], pin1=a[1], ref2=b[0], pin2=b[1]),
                      label=f"{a[0]}:{a[1]}->{b[0]}:{b[1]}") for a, b in pairs]
        v = f"perf_connect:{name}:{sname}"
        F.time3_chain(v, p, steps)
        R.run_sequence("perf-05", v, steps, p, script="probe-perf/perf_connect.py",
                       also=["perf-07"], note=f"{len(steps)}-call chain on one file ({sname})")

F.attach_timings()
R.end()
