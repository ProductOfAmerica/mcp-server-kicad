# probeB copy of pr57pt/probe-hierarchy/h03_alias_two_calls.py (header swapped to the probeB shims; fixture code unchanged).
"""H03: the 'netlist-neutral' first call of h01_power_FRESH is not harmless. P accepts
wire_pins_to_net(R1:1 -> SIG) although R1:1 is on +5V through sheet pin PWR (the label SIG
becomes an alias of +5V); a later, individually 'safe' call puts a signal pin on SIG and lands it
on +5V.

Root: sheet C with pin PWR; R1:1 wired to it (h01 geometry); R4 (50.8, 50.8) free.
Child: R31:1 on hier label PWR and power +5V.
Calls (root): 1) wire_pins_to_net([R1:1], 'SIG'); 2) wire_pins_to_net([R4:1], 'SIG').
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("h03_alias_two_calls", __file__)
from hlib import HERE, Log, Proj, netlist, run_seq, show  # noqa: E402

log = Log(HERE / "h03_alias_two_calls.out")

pj = Proj("h03_alias")
r = pj.root
ch = pj.child("c.kicad_sch")
_, w = r.sheet("C", ch, 152.4, 25.4, pins=[("PWR", "input", "left", 2)])
(p1, _) = r.R("R1", 127, 40.64)
r.wire(*p1, 152.4, p1[1])
r.wire(152.4, p1[1], *w["PWR"])
r.R("R4", 50.8, 50.8)
(c1, _) = ch.R(["R31"], 50.8, 50.8)
ch.wire(*c1, c1[0], 41.91)
ch.wire(c1[0], 41.91, c1[0], 38.1)
ch.label("PWR", c1[0], 41.91, kind="hierarchical_label")
ch.power("+5V", c1[0], 38.1)
pj.write()
show(netlist(pj.root.path), title="baseline root netlist", out=log)
pins = lambda ref: [{"reference": ref, "pin": "1"}]  # noqa: E731
run_seq(log, "h03_alias", pj.dir, pj.root.fname,
        [(pj.root.fname, "wire_pins_to_net", dict(pins=pins("R1"), label_text="SIG")),
         (pj.root.fname, "wire_pins_to_net", dict(pins=pins("R4"), label_text="SIG"))],
        pin_nodes=[("R1", "1"), ("R31", "1"), ("R4", "1")],
        ideal="call 1 should refuse (or say R1:1 is on a net named elsewhere); R4 must not reach +5V")
log.save()
