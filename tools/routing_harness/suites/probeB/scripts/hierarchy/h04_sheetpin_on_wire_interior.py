# probeB copy of pr57pt/probe-hierarchy/h04_sheetpin_on_wire_interior.py (header swapped to the probeB shims; fixture code unchanged).
"""H04: the root pin reaches sheet pin PWR through a wire whose INTERIOR passes over the sheet pin
(no wire ends there). kicad-cli 9 attaches a sheet pin to the first wire within 1 IU through
SCH_LABEL_BASE::UpdateDanglingState (SCH_SHEET_PIN derives from SCH_HIERLABEL; digest G15/F2).
M's component model captures LABELS on wire interiors but lists sheet pins separately, so does
M's port refusal still fire?

Root: sheet C (152.4, 25.4) 30.48 x 20.32, pin PWR at (152.4, 30.48).
  geometry 'edge': R1:1 (127, 36.83) -> (152.4, 36.83) -> (152.4, 27.94): the vertical wire runs
                   along the sheet's left edge and passes over the pin.
  geometry 'into': R1:1 -> (139.7, 36.83) -> (139.7, 30.48) -> (165.1, 30.48): the last wire
                   passes through the pin into the sheet body.
  R2:1 with local label N1; R3:1 with power GND.
Child: R31:1 on hier label PWR and power +5V.
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("h04_sheetpin_on_wire_interior", __file__)
from hlib import HERE, Log, Proj, netlist, run, show  # noqa: E402

log = Log(HERE / "h04_sheetpin_on_wire_interior.out")


def build(geom):
    pj = Proj(f"h04_{geom}")
    r = pj.root
    ch = pj.child("c.kicad_sch")
    _, w = r.sheet("C", ch, 152.4, 25.4, pins=[("PWR", "input", "left", 2)])
    (p1, _) = r.R("R1", 127, 40.64)
    if geom == "edge":
        r.wire(*p1, 152.4, p1[1])
        r.wire(152.4, p1[1], 152.4, 27.94)
    else:
        r.wire(*p1, 139.7, p1[1])
        r.wire(139.7, p1[1], 139.7, 30.48)
        r.wire(139.7, 30.48, 165.1, 30.48)
    (q1, _) = r.R("R2", 76.2, 50.8)
    r.wire(*q1, q1[0], round(q1[1] - 5.08, 4))
    r.label("N1", q1[0], round(q1[1] - 5.08, 4))
    (g1, _) = r.R("R3", 101.6, 50.8)
    r.wire(*g1, g1[0], round(g1[1] - 5.08, 4))
    r.power("GND", g1[0], round(g1[1] - 5.08, 4))
    (c1, _) = ch.R(["R31"], 50.8, 50.8)
    ch.wire(*c1, c1[0], 41.91)
    ch.wire(c1[0], 41.91, c1[0], 38.1)
    ch.label("PWR", c1[0], 41.91, kind="hierarchical_label")
    ch.power("+5V", c1[0], 38.1)
    pj.write()
    return pj


NODES = [("R1", "1"), ("R31", "1"), ("R2", "1"), ("R3", "1")]
for geom in ("edge", "into"):
    pj = build(geom)
    log(f"\n######## geometry {geom}")
    show(netlist(pj.root.path), title="baseline root netlist (is R1:1 on +5V through the sheet pin?)",
         out=log)
    for N, other in (("GND", ("R3", "1")), ("N1", ("R2", "1"))):
        run(log, f"h04_{geom}_{N}", pj.dir, pj.root.fname, pj.root.fname, "wire_pins_to_net",
            dict(pins=[{"reference": "R1", "pin": "1"}], label_text=N),
            allowed=[("R1", "1"), other],
            ideal="refuse: R1:1 is on +5V through the sheet pin (KiCad attaches it to the wire)",
            pin_nodes=NODES)
log.save()
