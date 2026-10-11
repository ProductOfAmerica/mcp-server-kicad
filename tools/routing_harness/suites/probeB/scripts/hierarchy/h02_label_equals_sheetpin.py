# probeB copy of pr57pt/probe-hierarchy/h02_label_equals_sheetpin.py (header swapped to the probeB shims; fixture code unchanged).
"""H02: wire_pins_to_net on the root pin wired to sheet pin PWR, with label_text == 'PWR' (the
sheet pin's own name).

Root: sheet "C" (c.kicad_sch), pin PWR at (152.4, 30.48); R1:1 wired to it (as in h01).
  extra 'r5': R5 (50.8, 50.8) pin 1 carries a LOCAL label PWR on a separate subgraph.
Child: R31:1 on hier label PWR, plus power +5V (variant power) or nothing (variant plain).
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("h02_label_equals_sheetpin", __file__)
from hlib import HERE, Log, Proj, netlist, run, show  # noqa: E402

log = Log(HERE / "h02_label_equals_sheetpin.out")


def build(variant, r5):
    pj = Proj(f"h02_{variant}_{'r5' if r5 else 'alone'}")
    r = pj.root
    ch = pj.child("c.kicad_sch")
    _, w = r.sheet("C", ch, 152.4, 25.4, pins=[("PWR", "input", "left", 2)])
    (p1, _) = r.R("R1", 127, 40.64)
    r.wire(*p1, 152.4, p1[1])
    r.wire(152.4, p1[1], *w["PWR"])
    if r5:
        (q1, _) = r.R("R5", 50.8, 50.8)
        r.wire(*q1, q1[0], round(q1[1] - 5.08, 4))
        r.label("PWR", q1[0], round(q1[1] - 5.08, 4))
    (c1, _) = ch.R(["R31"], 50.8, 50.8)
    ch.wire(*c1, c1[0], 41.91)
    ch.wire(c1[0], 41.91, c1[0], 38.1)
    ch.label("PWR", c1[0], 41.91, kind="hierarchical_label")
    if variant == "power":
        ch.power("+5V", c1[0], 38.1)
    pj.write()
    return pj


NODES = [("R1", "1"), ("R31", "1"), ("R5", "1")]
for variant in ("power", "plain"):
    for r5 in (False, True):
        pj = build(variant, r5)
        log(f"\n######## child side {variant}; root has a separate local label PWR on R5: {r5}")
        show(netlist(pj.root.path), title="baseline root netlist", out=log)
        if not r5:
            ideal = ("no merge possible; a no-op ('already wired to sheet pin PWR') or a harmless "
                     "label; a refusal is conservative")
        elif variant == "power":
            ideal = "refuse: R1:1 is on +5V; label PWR would join R5's /PWR net into +5V"
        else:
            ideal = "policy: R1's net is named only by the port chain; joining /PWR is the request"
        run(log, f"h02_{variant}_{'r5' if r5 else 'alone'}", pj.dir, pj.root.fname, pj.root.fname,
            "wire_pins_to_net", dict(pins=[{"reference": "R1", "pin": "1"}], label_text="PWR"),
            allowed=[("R1", "1")] + ([("R5", "1")] if r5 else []), ideal=ideal, pin_nodes=NODES)
log.save()
