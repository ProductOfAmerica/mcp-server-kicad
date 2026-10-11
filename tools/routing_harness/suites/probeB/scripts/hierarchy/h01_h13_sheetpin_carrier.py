# probeB copy of pr57pt/probe-hierarchy/h01_h13_sheetpin_carrier.py (header swapped to the probeB shims; fixture code unchanged).
"""H01 (digest H13): a ROOT pin wired only to a sheet pin whose child-side hierarchical label sits
on a strongly named net. wire_pins_to_net on the root pin with a new label text.

Root: sheet "C" (c.kicad_sch), pin PWR on the left edge at (152.4, 30.48).
  R1 (127, 40.64): pin 1 (127, 36.83) -> (152.4, 36.83) -> (152.4, 30.48) [ends ON the sheet pin]
     (the wire leaves the pin sideways, so the outward 'up' stub is free; pre-split, no junctions)
  R2 (76.2, 50.8): pin 1 with local label N1 at a stub end (a separate named parent net)
  R3 (101.6, 50.8): pin 1 with power GND at a stub end
Child c.kicad_sch, R31 (50.8, 50.8): pin 1 -> (50.8, 41.91) [hier label PWR] -> (50.8, 38.1) [X]
  where X is: power +5V | global label VBUS | local label LOC | nothing (plain port chain)
Calls (schematic_path = root): label_text FRESH (new name), N1 (existing parent local), GND
(existing parent power).
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("h01_h13_sheetpin_carrier", __file__)
from hlib import HERE, Log, Proj, netlist, run, show  # noqa: E402

log = Log(HERE / "h01_h13_sheetpin_carrier.out")


def build(variant):
    pj = Proj(f"h01_{variant}")
    r = pj.root
    ch = pj.child("c.kicad_sch")
    _, w = r.sheet("C", ch, 152.4, 25.4, pins=[("PWR", "input", "left", 2)])
    (p1, _) = r.R("R1", 127, 40.64)
    r.wire(*p1, 152.4, p1[1])
    r.wire(152.4, p1[1], *w["PWR"])
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
    if variant == "power":
        ch.power("+5V", c1[0], 38.1)
    elif variant == "global":
        ch.label("VBUS", c1[0], 38.1, kind="global_label")
    elif variant == "local":
        ch.label("LOC", c1[0], 38.1)
    pj.write()
    return pj


NODES = [("R1", "1"), ("R31", "1"), ("R2", "1"), ("R3", "1")]
for variant in ("power", "global", "local", "plain"):
    pj = build(variant)
    log(f"\n######## child side of sheet pin PWR: {variant}")
    show(netlist(pj.root.path), title="baseline root netlist", out=log)
    child_named = variant != "plain"
    for N, other in (("FRESH", None), ("N1", ("R2", "1")), ("GND", ("R3", "1"))):
        allowed = [("R1", "1")] + ([other] if other else [])
        if child_named:
            ideal = ("refuse: R1:1 is already on a differently named net (the child's "
                     f"{variant} name), invisible from the root file")
            if N == "FRESH":
                ideal = ("refuse or warn: no merge is possible, but 'FRESH' becomes an alias of the "
                         "child-named net")
        else:
            ideal = ("policy: the net is named only by the port chain; joining it to N is the "
                     "requested join (M refuses by policy)")
        run(log, f"h01_{variant}_{N}", pj.dir, pj.root.fname, pj.root.fname, "wire_pins_to_net",
            dict(pins=[{"reference": "R1", "pin": "1"}], label_text=N),
            allowed=allowed, join=(("R1", "1"), other) if other else None, ideal=ideal,
            pin_nodes=NODES)
log.save()
