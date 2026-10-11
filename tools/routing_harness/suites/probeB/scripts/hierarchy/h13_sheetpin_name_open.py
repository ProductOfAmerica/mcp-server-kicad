# probeB copy of pr57pt/probe-hierarchy/h13_sheetpin_name_open.py (header swapped to the probeB shims; fixture code unchanged).
"""H13: label text equal to a sheet pin's name, on a DIFFERENT parent net. KiCad 9 does not join a
sheet pin to a same-named local label on its sheet (digest H1, except via secondary drivers), so
the requested 'put R2:1 on SDA' silently does not reach the port's net.

Root: sheet C, pin SDA at (152.4, 30.48); R1:1 wired to it (h01 geometry); R2 (76.2, 50.8) free.
Child: R31:1 on hier label SDA (plain) | variant 'power': plus power +3V3 on that net.
Call (root): wire_pins_to_net([R2:1], 'SDA').
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("h13_sheetpin_name_open", __file__)
from hlib import HERE, Log, Proj, netlist, run, show  # noqa: E402

log = Log(HERE / "h13_sheetpin_name_open.out")


def build(variant):
    pj = Proj(f"h13_{variant}")
    r = pj.root
    ch = pj.child("c.kicad_sch")
    _, w = r.sheet("C", ch, 152.4, 25.4, pins=[("SDA", "bidirectional", "left", 2)])
    (p1, _) = r.R("R1", 127, 40.64)
    r.wire(*p1, 152.4, p1[1])
    r.wire(152.4, p1[1], *w["SDA"])
    r.R("R2", 76.2, 50.8)
    (c1, _) = ch.R(["R31"], 50.8, 50.8)
    ch.wire(*c1, c1[0], 41.91)
    ch.wire(c1[0], 41.91, c1[0], 38.1)
    ch.label("SDA", c1[0], 41.91, kind="hierarchical_label", shape="bidirectional")
    if variant == "power":
        ch.power("+3V3", c1[0], 38.1)
    pj.write()
    return pj


for variant in ("plain", "power"):
    pj = build(variant)
    log(f"\n######## child side {variant}")
    show(netlist(pj.root.path), title="baseline root netlist", out=log)
    run(log, f"h13_{variant}", pj.dir, pj.root.fname, pj.root.fname, "wire_pins_to_net",
        dict(pins=[{"reference": "R2", "pin": "1"}], label_text="SDA"),
        allowed=[("R2", "1"), ("R1", "1")], join=(("R2", "1"), ("R1", "1")),
        ideal="if the caller means the port's net, R2:1 must reach it (it does not); at least say so",
        pin_nodes=[("R2", "1"), ("R1", "1"), ("R31", "1")])
log.save()
