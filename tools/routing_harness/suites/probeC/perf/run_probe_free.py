"""perf-01 / perf-02 / perf-06 / perf-07 probe timing calls on the augmented sheets (fixtures/probe/
<name>.aug.kicad_sch and tinytapeout-demo.k200.kicad_sch, rebuilt with perf_augment.py /
perf_k200.py code: connector origins equal to the originals'), on M2 and K through rlib.

  perf_wptn.py      wire_pins_to_net(J9001:1..k, 'PERF_K<k>'), k = 1, 10, 50, four sheets
                    (order ampli_ht, tinytapeout-demo, fpga-hp-banks, fpga-power as the script's).
  perf_refusal.py   tinytapeout-demo.aug, J9001:50 first put on PERF_NAMED with a raw stub and label
                    (harness writers), then wire_pins_to_net([J9001:50, J9001:1..49], 'PERF_OTHER').
  perf_k200.py      tinytapeout-demo.k200: wire_pins_to_net of the first k of J9001, J9004..J9006
                    pins, k = 100, 200 ('PERF_K<k>'). perf_k200_rerun.py repeats the k = 200 call
                    (same sheet, same pins, same name): one variant.
Each variant: min of 3 unjudged timing runs per design (pfix.time3), then one judged run per design.
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

R.begin("perf", __file__, tag="perf_probe_free")

for name in ("ampli_ht", "tinytapeout-demo", "fpga-hp-banks", "fpga-power"):
    p = F.fx(f"probe/{name}.aug.kicad_sch", f"{name}.aug")
    for k in (1, 10, 50):
        kw = {"pins": [{"reference": "J9001", "pin": str(i)} for i in range(1, k + 1)],
              "label_text": f"PERF_K{k}"}
        v = f"perf_wptn:{name}:k{k}"
        F.time3(v, "wire_pins_to_net", p, kw)
        R.run_case("perf-01", v, "wire_pins_to_net", p, kw, script="probe-perf/perf_wptn.py",
                   also=["perf-06", "perf-07"], note=f"{k} free J9001 pins, fresh name")

# perf_refusal.py
p = F.fx("probe/tinytapeout-demo.aug.kicad_sch", "refusal_base")
it = M2.pin_positions(str(p), "J9001")["50"][0]
x, y, out = it
dx, dy = {"right": (1, 0), "left": (-1, 0), "up": (0, -1), "down": (0, 1)}[out]
H.wire(str(p), x, y, x + 2.54 * dx, y + 2.54 * dy)
H.label(str(p), "PERF_NAMED", x + 2.54 * dx, y + 2.54 * dy, rot=0 if dx > 0 else 180)
kw = {"pins": [{"reference": "J9001", "pin": "50"}] + [{"reference": "J9001", "pin": str(i)}
                                                       for i in range(1, 50)],
      "label_text": "PERF_OTHER"}
v = "perf_refusal:tinytapeout-demo.aug:J9001:50-on-PERF_NAMED"
F.time3(v, "wire_pins_to_net", p, kw)
R.run_case("perf-01", v, "wire_pins_to_net", p, kw, script="probe-perf/perf_refusal.py",
           also=["perf-07"], note=f"J9001:50 at ({x}, {y}) outward {out} first put on PERF_NAMED;"
           " both designs must refuse the whole call byte-identically")

# perf_k200.py (+ perf_k200_rerun.py)
p = F.fx("probe/tinytapeout-demo.k200.kicad_sch", "k200")
pins_all = [("J9001", str(i)) for i in range(1, 51)] + [
    (r, str(i)) for r in ("J9004", "J9005", "J9006") for i in range(1, 51)]
for k in (100, 200):
    kw = {"pins": [{"reference": r, "pin": n} for r, n in pins_all[:k]], "label_text": f"PERF_K{k}"}
    v = f"perf_k200:tinytapeout-demo.k200:k{k}"
    F.time3(v, "wire_pins_to_net", p, kw)
    R.run_case("perf-02", v, "wire_pins_to_net", p, kw, script="probe-perf/perf_k200.py",
               also=["perf-07"], note=f"{k} free pins in one call" + (
                   "; perf_k200_rerun.py repeats this exact call" if k == 200 else ""))

F.attach_timings()
R.end()
