"""perf-01 / perf-02 / perf-06 verifier timing calls on the .free fixtures (fixtures/verify/
<name>.free.kicad_sch and tinytapeout-demo.free200.kicad_sch, rebuilt with vp_fixtures.py code:
J8001 origin x equal to the original's), on M2 and K through rlib.

  vp_timing.py         wire_pins_to_net(J8001:1..k, 'VP_K<k>'), k = 1, 10, 50, four sheets.
  vp_refusal_k200.py R tinytapeout-demo.free, J8001:50 first put on VP_NAMED with a raw stub and
                       label (harness writers), then wire_pins_to_net([J8001:50, J8001:1..49],
                       'VP_OTHER'); must refuse byte-identically.
  vp_refusal_k200.py S tinytapeout-demo.free200: the first k of J8001..J8004's pins, k = 100, 200
                       ('VP_K<k>').
  vp_fastp.py          its two free-pin identity calls: wire_pins_to_net(J8001:1..50, 'VP_FAST') on
                       ampli_ht.free and tinytapeout-demo.free (P's half of the check).
Each variant: min of 3 unjudged timing runs per design, then one judged run per design.
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib as R  # noqa: E402
from rlib import H, M2  # noqa: E402

import pfix as F  # noqa: E402

R.begin("perf", __file__, tag="perf_vp_free")

for name in ("ampli_ht", "tinytapeout-demo", "fpga-hp-banks", "fpga-power"):
    p = F.fx(f"verify/{name}.free.kicad_sch", f"{name}.free")
    for k in (1, 10, 50):
        kw = {"pins": [{"reference": "J8001", "pin": str(i)} for i in range(1, k + 1)],
              "label_text": f"VP_K{k}"}
        v = f"vp_timing:{name}.free:k{k}"
        F.time3(v, "wire_pins_to_net", p, kw)
        R.run_case("perf-01", v, "wire_pins_to_net", p, kw, script="verify-probe-perf/vp_timing.py",
                   also=["perf-06"], note=f"{k} free J8001 pins, fresh name")

# vp_refusal_k200.py R
p = F.fx("verify/tinytapeout-demo.free.kicad_sch", "ref_base")
x, y, out = M2.pin_positions(str(p), "J8001")["50"][0]
dx = 2.54 if out == "right" else -2.54
H.wire(str(p), x, y, x + dx, y)
H.label(str(p), "VP_NAMED", x + dx, y, rot=0 if dx > 0 else 180)
kw = {"pins": [{"reference": "J8001", "pin": "50"}] + [{"reference": "J8001", "pin": str(i)}
                                                       for i in range(1, 50)],
      "label_text": "VP_OTHER"}
v = "vp_refusal_k200:R:tinytapeout-demo.free:J8001:50-on-VP_NAMED"
F.time3(v, "wire_pins_to_net", p, kw)
R.run_case("perf-01", v, "wire_pins_to_net", p, kw, script="verify-probe-perf/vp_refusal_k200.py",
           note=f"J8001:50 at ({x}, {y}) outward {out} first put on VP_NAMED; must refuse")

# vp_refusal_k200.py S
p = F.fx("verify/tinytapeout-demo.free200.kicad_sch", "free200")
allpins = [{"reference": r, "pin": str(i)} for r in ("J8001", "J8002", "J8003", "J8004")
           for i in range(1, 51)]
for k in (100, 200):
    kw = {"pins": allpins[:k], "label_text": f"VP_K{k}"}
    v = f"vp_refusal_k200:S:tinytapeout-demo.free200:k{k}"
    F.time3(v, "wire_pins_to_net", p, kw)
    R.run_case("perf-02", v, "wire_pins_to_net", p, kw, script="verify-probe-perf/vp_refusal_k200.py",
               note=f"{k} free pins in one call")

# vp_fastp.py free-pin identity calls
for name in ("ampli_ht", "tinytapeout-demo"):
    p = F.fx(f"verify/{name}.free.kicad_sch", f"{name}.free_fastp")
    kw = {"pins": [{"reference": "J8001", "pin": str(i)} for i in range(1, 51)], "label_text": "VP_FAST"}
    R.run_case("perf-01", f"vp_fastp:{name}.free:k50", "wire_pins_to_net", p, kw,
               script="verify-probe-perf/vp_fastp.py",
               note="vp_fastp identity check, P's call (FastP itself is not re-run)")

F.attach_timings()
R.end()
