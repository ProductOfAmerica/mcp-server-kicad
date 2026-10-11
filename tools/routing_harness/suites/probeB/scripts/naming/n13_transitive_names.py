# probeB copy of pr57pt/probe-naming/n13_transitive_names.py: header swapped to the probeB shims, the design loop is M2/K, step() calls carry vid=/stepno=; fixture code unchanged.
"""N13: names that are the same net transitively. R3:1's net carries two labels, 'A' and 'N', so in
KiCad every 'A' and every 'N' on the sheet is one net (digest N03, F18). P's model unions by name
(builder I01); M's components are geometric and compare per-component name sets (M I07).

R1:1 carries 'A' (own wire left to (93.98, 97.79)); R2:1 carries 'N' (wire up to (127, 92.71));
R3:1 is wired up to (152.4, 120.65) with label 'A' there and a second wire right to
(157.48, 120.65) with label 'N'.
  w  wire [R1:1] -> 'N'        (KiCad: R1:1 is already on N)
  c  connect_pins(R1:1, R2:1)  (KiCad: already connected)
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("n13_transitive_names", __file__)
from nlib import CP, H, Fixture, W, dump, pos, show_nets, step  # noqa: E402


def build(fx):
    p = fx.path("n13.kicad_sch")
    H.project.create_schematic(p)
    H.place(p, "Device:R", "R1", 101.6, 101.6)
    H.place(p, "Device:R", "R2", 127, 101.6)
    H.place(p, "Device:R", "R3", 152.4, 127)
    x1, y1 = pos(p, "R1", "1")
    x2, y2 = pos(p, "R2", "1")
    x3, y3 = pos(p, "R3", "1")
    H.wire(p, x1, y1, 93.98, y1)
    H.label(p, "A", 93.98, y1)
    H.wire(p, x2, y2, x2, 92.71)
    H.label(p, "N", x2, 92.71)
    H.wire(p, x3, y3, x3, 120.65)
    H.label(p, "A", x3, 120.65)
    H.wire(p, x3, 120.65, 157.48, 120.65)
    H.label(p, "N", 157.48, 120.65)
    return p


focus = [("R1", "1"), ("R2", "1"), ("R3", "1")]
for tag in ("w", "c"):
    fx = Fixture(f"n13_{tag}")
    p = build(fx)
    show_nets(p, f"{tag} fixture", focus=focus)
    for d in rlib.DESIGNS:
        c = fx.clone(d)
        s = c.path("n13.kicad_sch")
        if tag == "w":
            step(d, "N13w: wire_pins_to_net [R1:1 on A] -> N  (A and N share R3's net)", s, [s],
                 "wire_pins_to_net", W([("R1", "1")], "N", s), targets=[("R1", "1")], N="N", vid=tag)
        else:
            step(d, "N13c: connect_pins R1:1 (A) -> R2:1 (N)  (A and N share R3's net)", s, [s],
                 "connect_pins", CP("R1", "1", "R2", "1", s), pair=(("R1", "1"), ("R2", "1")), vid=tag)
        show_nets(s, f"[{d}] {tag} after", focus=focus)

dump(__file__)
