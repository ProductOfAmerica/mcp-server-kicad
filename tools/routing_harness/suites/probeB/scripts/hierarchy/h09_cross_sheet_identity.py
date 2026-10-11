# probeB copy of pr57pt/probe-hierarchy/h09_cross_sheet_identity.py (header swapped to the probeB shims; fixture code unchanged).
"""H09: global labels and power symbols that tie names together on ANOTHER sheet. From the edited
child file the pin looks like it is on a differently named net, but in the project it is already
on N. Both designs refuse by name; is that refusing a legitimate request?

9a  child: R31:1 on global label G.   root: R9:1 on power +5V AND global label G (same wire).
    call (child): wire_pins_to_net([R31:1], '+5V')    (R31:1 is already on +5V via G)
9b  child: R31:1 on power +5V.        root: R9:1 on power +5V AND global label VBUS.
    call (child): wire_pins_to_net([R31:1], 'VBUS')   (R31:1 is already on the VBUS/+5V net)
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("h09_cross_sheet_identity", __file__)
from hlib import HERE, Log, Proj, netlist, run, show  # noqa: E402

log = Log(HERE / "h09_cross_sheet_identity.out")


def build(case):
    pj = Proj(f"h09_{case}")
    r = pj.root
    ch = pj.child("c.kicad_sch")
    r.sheet("C", ch, 152.4, 25.4)
    (g1, _) = r.R("R9", 50.8, 50.8)
    top = (g1[0], round(g1[1] - 5.08, 4))
    r.wire(*g1, *top)
    r.power("+5V", *top)
    r.wire(*top, round(top[0] + 5.08, 4), top[1])
    r.label("G" if case == "9a" else "VBUS", round(top[0] + 5.08, 4), top[1], kind="global_label")
    (c1, _) = ch.R(["R31"], 50.8, 50.8)
    ch.wire(*c1, 45.72, c1[1])
    if case == "9a":
        ch.label("G", 45.72, c1[1], kind="global_label")
    else:
        ch.power("+5V", 45.72, c1[1])
    pj.write()
    return pj


for case, N in (("9a", "+5V"), ("9b", "VBUS")):
    pj = build(case)
    log(f"\n######## {case}")
    show(netlist(pj.root.path), title="baseline root netlist", out=log)
    run(log, f"h09_{case}", pj.dir, pj.root.fname, "c.kicad_sch", "wire_pins_to_net",
        dict(pins=[{"reference": "R31", "pin": "1"}], label_text=N), allowed=[("R31", "1")],
        ideal="no-op: R31:1 is already on that net through the other sheet (a sheet-local check "
              "cannot know; refusal is the safe fallback)",
        pin_nodes=[("R31", "1"), ("R9", "1")])
log.save()
