# probeB copy of pr57pt/probe-naming/n15_autolabel_text_clash.py: header swapped to the probeB shims, the design loop is M2/K, step() calls carry vid=/stepno=; fixture code unchanged.
"""N15: connect_pins' auto label text already names another net. P line 66 restricts the label only
by geometric touch ("placed only where it touches nothing on another net"); a same-text label
elsewhere joins by NAME (digest N01). The P prototype skips the label in this case (I26c, listed by
its builder as a favourable addition beyond P's text). For R1/R2 both free, literal P adds the
label exactly as B does, so B's outcome is literal P's.

R3:1's net carries local 'Net-(R1-1)' (e.g. left behind by an earlier connect_pins(R1:1, ...)
before R1 was moved). connect_pins(R1:1, R2:1) with R1:1/R2:1 free and aligned.
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("n15_autolabel_text_clash", __file__)
from nlib import CP, H, Fixture, dump, pos, show_nets, step  # noqa: E402

fx = Fixture("n15")
p = fx.path("n15.kicad_sch")
H.project.create_schematic(p)
H.place(p, "Device:R", "R1", 101.6, 101.6)
H.place(p, "Device:R", "R2", 127, 101.6)
H.place(p, "Device:R", "R3", 152.4, 127)
x3, y3 = pos(p, "R3", "1")
H.wire(p, x3, y3, x3, 120.65)
H.label(p, "Net-(R1-1)", x3, 120.65)
focus = [("R1", "1"), ("R2", "1"), ("R3", "1")]
show_nets(p, "fixture", focus=focus)
for d in rlib.DESIGNS:
    c = fx.clone(d)
    s = c.path("n15.kicad_sch")
    step(d, "N15 connect_pins R1:1 -> R2:1 (another net already carries 'Net-(R1-1)')", s, [s],
         "connect_pins", CP("R1", "1", "R2", "1", s), pair=(("R1", "1"), ("R2", "1")), vid="n15")
    show_nets(s, f"[{d}] after", focus=focus)

dump(__file__)
