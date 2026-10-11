# probeB copy of pr57pt/probe-hierarchy/h14_port_net_already_on_n.py (header swapped to the probeB shims; fixture code unchanged).
"""H14: a parent pin already on local label BUS_A, whose net also reaches sheet pin H. Wiring it to
'BUS_A' should be a no-op (KiCad: it is on /BUS_A). M's port rule (as corrected: refuse whenever
the component holds a sheet pin, whatever label_text is) is checked before the names. With
all-or-nothing calls, one such pin also blocks a free pin in the same call.

Root: sheet C, pin H at (152.4, 30.48); R1:1 wired to it with local label BUS_A on the wire
interior... placed at a wire END (pre-split): R1:1 (127,36.83) -> (139.7,36.83) [label BUS_A]
-> (152.4,36.83) -> (152.4,30.48). R9 (76.2, 50.8) free.
Child: R31:1 on hier label H (plain).
Calls (root): 14a wire_pins_to_net([R1:1], 'BUS_A'); 14b wire_pins_to_net([R1:1, R9:1], 'BUS_A').
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("h14_port_net_already_on_n", __file__)
from hlib import HERE, Log, Proj, netlist, run, show  # noqa: E402

log = Log(HERE / "h14_port_net_already_on_n.out")

pj = Proj("h14")
r = pj.root
ch = pj.child("c.kicad_sch")
_, w = r.sheet("C", ch, 152.4, 25.4, pins=[("H", "input", "left", 2)])
(p1, _) = r.R("R1", 127, 40.64)
r.wire(*p1, 139.7, p1[1])
r.wire(139.7, p1[1], 152.4, p1[1])
r.wire(152.4, p1[1], *w["H"])
r.label("BUS_A", 139.7, p1[1])
r.R("R9", 76.2, 50.8)
(c1, _) = ch.R(["R31"], 50.8, 50.8)
ch.wire(*c1, c1[0], 41.91)
ch.label("H", c1[0], 41.91, kind="hierarchical_label")
pj.write()
show(netlist(pj.root.path), title="baseline root netlist", out=log)
NODES = [("R1", "1"), ("R31", "1"), ("R9", "1")]
run(log, "h14a_single", pj.dir, pj.root.fname, pj.root.fname, "wire_pins_to_net",
    dict(pins=[{"reference": "R1", "pin": "1"}], label_text="BUS_A"), allowed=[("R1", "1")],
    ideal="no-op: R1:1 is already on /BUS_A", pin_nodes=NODES)
run(log, "h14b_batch", pj.dir, pj.root.fname, pj.root.fname, "wire_pins_to_net",
    dict(pins=[{"reference": "R1", "pin": "1"}, {"reference": "R9", "pin": "1"}], label_text="BUS_A"),
    allowed=[("R1", "1"), ("R9", "1")], join=(("R1", "1"), ("R9", "1")),
    ideal="R1:1 no-op, R9:1 joins /BUS_A", pin_nodes=NODES)
log.save()
