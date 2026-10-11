# probeB copy of pr57pt/probe-naming/n10_case_only.py: header swapped to the probeB shims, the design loop is M2/K, step() calls carry vid=/stepno=; fixture code unchanged.
"""N10: case-only name differences on local labels (item 7). KiCad never joins case variants
(digest N01); power Values and hidden pin names were covered in n06 (f) and n07 (a2, b).

R1:1 (101.6, 97.79) wired left to (93.98, 97.79) for its own label; R2:1 (127, 97.79) free and
aligned; R7:1 carries local 'VCC'.
  c1  R1:1 on local 'vcc'.  wire [R1:1] -> 'VCC'        (would merge the vcc and VCC nets)
  c2  R1:1 free.            wire [R1:1] -> 'Vcc'        (a third, separate net; does anyone warn?)
  c3  R1:1 on local 'sig', R2:1 on local 'SIG' (wire up).  connect_pins(R1:1, R2:1)
  c4  R1:1 free, a local label 'vcc' sits at R1:1's 1x stub end (no wire).  wire [R1:1] -> 'VCC'
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("n10_case_only", __file__)
from nlib import CP, H, Fixture, W, dump, pos, show_nets, step  # noqa: E402


def build(fx, tag):
    p = fx.path("n10.kicad_sch")
    H.project.create_schematic(p)
    H.place(p, "Device:R", "R1", 101.6, 101.6)
    H.place(p, "Device:R", "R2", 127, 101.6)
    H.place(p, "Device:R", "R7", 152.4, 127)
    x1, y1 = pos(p, "R1", "1")
    x2, y2 = pos(p, "R2", "1")
    x7, y7 = pos(p, "R7", "1")
    H.wire(p, x7, y7, x7, 120.65)
    H.label(p, "VCC", x7, 120.65)
    if tag in ("c1", "c3"):
        H.wire(p, x1, y1, 93.98, y1)
        H.label(p, "vcc" if tag == "c1" else "sig", 93.98, y1)
    if tag == "c3":
        H.wire(p, x2, y2, x2, 92.71)
        H.label(p, "SIG", x2, 92.71)
    if tag == "c4":
        H.label(p, "vcc", x1, 95.25)
    return p


focus = [("R1", "1"), ("R2", "1"), ("R7", "1")]
for tag in ("c1", "c2", "c3", "c4"):
    fx = Fixture(f"n10_{tag}")
    p = build(fx, tag)
    show_nets(p, f"{tag} fixture", focus=focus)
    for d in rlib.DESIGNS:
        c = fx.clone(d)
        s = c.path("n10.kicad_sch")
        if tag == "c3":
            step(d, "N10c3: connect_pins R1:1 ('sig') -> R2:1 ('SIG')", s, [s], "connect_pins",
                 CP("R1", "1", "R2", "1", s), pair=(("R1", "1"), ("R2", "1")), vid=tag)
        else:
            N = "Vcc" if tag == "c2" else "VCC"
            step(d, f"N10{tag}: wire_pins_to_net [R1:1] -> {N}", s, [s], "wire_pins_to_net",
                 W([("R1", "1")], N, s), targets=[("R1", "1")], N=N, vid=tag)
        show_nets(s, f"[{d}] {tag} after", focus=focus)

dump(__file__)
