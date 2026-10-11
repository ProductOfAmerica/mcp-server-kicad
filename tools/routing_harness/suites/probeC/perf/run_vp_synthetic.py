"""perf-03 / perf-04 mechanisms on minimal sheets built from scratch, code copied from
verify-probe-perf/vp_synthetic.py (S1, S2, S2r, S3, S3r, S4) and vp_nojunction.py (its S2 call with
label 'N'; its S3 call equals vp_synthetic S3), on M2 and K through rlib.
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib as R  # noqa: E402
from rlib import H, M2  # noqa: E402

import json  # noqa: E402
from pathlib import Path  # noqa: E402

R.begin("perf", __file__, tag="perf_vp_synthetic")
S = "verify-probe-perf/vp_synthetic.py"


def iu(v):
    return M2._iu(v)


def place(p, lib, ref, x, y):
    H.schematic.place_component(lib_id=lib, reference=ref, value=ref, x=x, y=y, schematic_path=p)


def expect(p, ref, num, xy):
    got = M2.pin_positions(p, ref)[num][0][:2]
    assert (iu(got[0]), iu(got[1])) == (iu(xy[0]), iu(xy[1])), (ref, num, got, xy)


scen = {}
# S1
p = H.fresh("syn_S1")
place(p, "Device:R", "R1", 101.6, 101.6)
place(p, "Device:R", "R2", 101.6, 92.71)
expect(p, "R1", "1", (101.6, 97.79))
expect(p, "R2", "2", (101.6, 96.52))
H.wire(p, 101.6, 97.79, 101.6, 96.52)
scen["S1"] = (p, "wire_pins_to_net", {"pins": [{"reference": "R1", "pin": "1"}], "label_text": "N_S1"},
              "perf-03", "perf-03 mechanism: 1.27 mm own wire to R2:2, shorter than the 2.54 mm stub")
# S2 / S2r
for tag, rev in (("S2", False), ("S2r", True)):
    p = H.fresh(f"syn_{tag}")
    place(p, "Device:R", "R1", 101.6, 101.6)
    place(p, "Device:R", "R2", 101.6, 86.36)
    expect(p, "R2", "2", (101.6, 90.17))
    a, b = (101.6, 97.79), (101.6, 90.17)
    if rev:
        a, b = b, a
    H.wire(p, *a, *b)
    scen[tag] = (p, "wire_pins_to_net", {"pins": [{"reference": "R1", "pin": "1"}], "label_text": f"N_{tag}"},
                 "perf-04", "perf-04 mechanism: 7.62 mm own wire, stub end on its interior"
                 + (" (wire drawn from R2:2)" if rev else " (wire drawn from R1:1)"))
# S3 / S3r
for tag, rev in (("S3", False), ("S3r", True)):
    p = H.fresh(f"syn_{tag}")
    place(p, "Device:R", "R1", 101.6, 101.6)
    place(p, "Device:R", "R4", 114.3, 101.6)
    place(p, "Device:R", "R5", 106.68, 88.9)
    expect(p, "R4", "1", (114.3, 97.79))
    expect(p, "R5", "2", (106.68, 92.71))
    a, b = (101.6, 97.79), (114.3, 97.79)
    if rev:
        a, b = b, a
    H.wire(p, *a, *b)
    scen[tag] = (p, "connect_pins", {"ref1": "R1", "pin1": "1", "ref2": "R5", "pin2": "2"},
                 "perf-04", "perf-04 connect mechanism: horizontal-first L corner on the own wire's"
                 " interior" + (" (own wire drawn from R4:1)" if rev else " (own wire drawn from R1:1)"))
# S4
p = H.fresh("syn_S4")
place(p, "Device:R", "R1", 101.6, 101.6)
place(p, "Connector_Generic:Conn_01x01", "J2", 106.68, 96.52)
place(p, "Device:R", "R6", 101.6, 86.36)
expect(p, "J2", "1", (101.6, 96.52))
expect(p, "R6", "2", (101.6, 90.17))
H.wire(p, 101.6, 97.79, 101.6, 96.52)
scen["S4"] = (p, "connect_pins", {"ref1": "R1", "pin1": "1", "ref2": "R6", "pin2": "2"},
              "perf-03", "perf-03 connect mechanism: straight route over the own wire and past J2:1")

for tag, (p, tool, kw, case, note) in scen.items():
    also = []
    if tag == "S3":
        note += "; vp_nojunction.py makes the same call on the same geometry"
    R.run_case(case, f"vp_synthetic:{tag}", tool, p, kw, script=S, also=also, note=note)

# vp_nojunction.py S2 call: same geometry as vp_synthetic S2, label 'N'
p = H.fresh("nj_S2")
place(p, "Device:R", "R1", 101.6, 101.6)
place(p, "Device:R", "R2", 101.6, 86.36)
H.wire(p, 101.6, 97.79, 101.6, 90.17)
R.run_case("perf-04", "vp_nojunction:S2 wptn R1:1 N", "wire_pins_to_net", p,
           {"pins": [{"reference": "R1", "pin": "1"}], "label_text": "N"},
           script="verify-probe-perf/vp_nojunction.py",
           note="P-only fix-variant script; its plain tool call (vp_synthetic S2 geometry, label N)")
(R.OUT / "perf").mkdir(parents=True, exist_ok=True)
(R.OUT / "perf" / "dups_vp_synthetic.json").write_text(json.dumps([
    {"variant": "vp_nojunction:S3 connect R1:1->R5:2", "same_as": "vp_synthetic:S3"}], indent=1),
    encoding="utf-8")
R.end()
