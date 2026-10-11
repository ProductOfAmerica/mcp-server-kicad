# probeB copy of pr57pt/probe-hierarchy/h05_connect_pins_ports.py (header swapped to the probeB shims; fixture code unchanged).
"""H05: connect_pins on the ROOT where one or both pins reach the child only through sheet pins.

Root: sheet C (152.4, 25.4) 30.48 x 30.48, left-edge pins A (152.4, 30.48) and B (152.4, 45.72).
  R1 (127, 40.64): pin 1 (127, 36.83) -> (139.7, 36.83) -> (139.7, 30.48) -> sheet pin A
  R6 (114.3, 60.96): pin 1 (114.3, 57.15) -> (139.7, 57.15) -> (139.7, 45.72) -> sheet pin B
  R3 (101.6, 50.8): pin 1 (101.6, 46.99) -> (96.52, 46.99) = power GND (root)
  R4 (88.9, 40.64): pin 1 (88.9, 36.83), free (aligned with R1:1: straight route)
Child: R31:1 on hier label A, R32:1 on hier label B; variant 'power': +5V on A's net, GND on B's;
variant 'plain': nothing else.
Calls: connect_pins(R1:1, R3:1), connect_pins(R1:1, R6:1), connect_pins(R1:1, R4:1).
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("h05_connect_pins_ports", __file__)
from hlib import HERE, Log, Proj, netlist, run, show  # noqa: E402

log = Log(HERE / "h05_connect_pins_ports.out")


def build(variant):
    pj = Proj(f"h05_{variant}")
    r = pj.root
    ch = pj.child("c.kicad_sch")
    _, w = r.sheet("C", ch, 152.4, 25.4, h=30.48, pins=[("A", "input", "left", 2),
                                                        ("B", "input", "left", 8)])
    (p1, _) = r.R("R1", 127, 40.64)
    r.wire(*p1, 139.7, p1[1])
    r.wire(139.7, p1[1], 139.7, w["A"][1])
    r.wire(139.7, w["A"][1], *w["A"])
    (s1, _) = r.R("R6", 114.3, 60.96)
    r.wire(*s1, 139.7, s1[1])
    r.wire(139.7, s1[1], 139.7, w["B"][1])
    r.wire(139.7, w["B"][1], *w["B"])
    (g1, _) = r.R("R3", 101.6, 50.8)
    r.wire(*g1, 96.52, g1[1])
    r.power("GND", 96.52, g1[1])
    r.R("R4", 88.9, 40.64)
    (a1, _) = ch.R(["R31"], 50.8, 50.8)
    ch.wire(*a1, a1[0], 41.91)
    ch.wire(a1[0], 41.91, a1[0], 38.1)
    ch.label("A", a1[0], 41.91, kind="hierarchical_label")
    (b1, _) = ch.R(["R32"], 76.2, 50.8)
    ch.wire(*b1, b1[0], 41.91)
    ch.wire(b1[0], 41.91, b1[0], 38.1)
    ch.label("B", b1[0], 41.91, kind="hierarchical_label")
    if variant == "power":
        ch.power("+5V", a1[0], 38.1)
        ch.power("GND", b1[0], 38.1)
    pj.write()
    return pj


NODES = [("R1", "1"), ("R31", "1"), ("R6", "1"), ("R32", "1"), ("R3", "1"), ("R4", "1")]
for variant in ("power", "plain"):
    pj = build(variant)
    log(f"\n######## child variant {variant}")
    show(netlist(pj.root.path), title="baseline root netlist", out=log)
    for other in ("R3", "R6", "R4"):
        if variant == "power" and other in ("R3", "R6"):
            ideal = "refuse: R1:1 is on +5V (through port A), the other pin on GND"
        elif other == "R4":
            ideal = "allow (R4 joins R1's net, the requested join); no rename of a named net"
        else:
            ideal = "policy: requested join of two port-chain nets (M refuses by policy)"
        run(log, f"h05_{variant}_R1_{other}", pj.dir, pj.root.fname, pj.root.fname, "connect_pins",
            dict(ref1="R1", pin1="1", ref2=other, pin2="1"),
            allowed=[("R1", "1"), (other, "1")], join=(("R1", "1"), (other, "1")), ideal=ideal,
            pin_nodes=NODES)
log.save()
