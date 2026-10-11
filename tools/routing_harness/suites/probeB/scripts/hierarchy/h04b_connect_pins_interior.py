# probeB copy of pr57pt/probe-hierarchy/h04b_connect_pins_interior.py (header swapped to the probeB shims; fixture code unchanged).
"""H04b: connect_pins with h04's 'edge' geometry (R1:1 reaches sheet pin PWR through a wire whose
interior passes over the pin; the child side is +5V) to R3:1 on root GND. R3's GND symbol hangs
off a leftward stub so the horizontal-first L route is geometrically clean.
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("h04b_connect_pins_interior", __file__)
from hlib import HERE, Log, Proj, netlist, run, show  # noqa: E402

log = Log(HERE / "h04b_connect_pins_interior.out")

pj = Proj("h04b_edge")
r = pj.root
ch = pj.child("c.kicad_sch")
_, w = r.sheet("C", ch, 152.4, 25.4, pins=[("PWR", "input", "left", 2)])
(p1, _) = r.R("R1", 127, 40.64)
r.wire(*p1, 152.4, p1[1])
r.wire(152.4, p1[1], 152.4, 27.94)
(g1, _) = r.R("R3", 101.6, 50.8)
r.wire(*g1, 96.52, g1[1])
r.power("GND", 96.52, g1[1])
(c1, _) = ch.R(["R31"], 50.8, 50.8)
ch.wire(*c1, c1[0], 41.91)
ch.wire(c1[0], 41.91, c1[0], 38.1)
ch.label("PWR", c1[0], 41.91, kind="hierarchical_label")
ch.power("+5V", c1[0], 38.1)
pj.write()
show(netlist(pj.root.path), title="baseline root netlist", out=log)
run(log, "h04b_edge_connect", pj.dir, pj.root.fname, pj.root.fname, "connect_pins",
    dict(ref1="R1", pin1="1", ref2="R3", pin2="1"), allowed=[("R1", "1"), ("R3", "1")],
    ideal="refuse: R1:1 is on +5V through the sheet pin, R3:1 on GND",
    pin_nodes=[("R1", "1"), ("R31", "1"), ("R3", "1")])
log.save()
