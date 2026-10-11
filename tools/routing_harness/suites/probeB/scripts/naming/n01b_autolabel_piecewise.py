# probeB copy of pr57pt/probe-naming/n01b_autolabel_piecewise.py: header swapped to the probeB shims, the design loop is M2/K, step() calls carry vid=/stepno=; fixture code unchanged.
"""N01b: building one net piecewise with connect_pins.

  step 1 connect_pins(R1:1, R2:1)   P/B add auto label 'Net-(R1-1)'
  step 2 connect_pins(R3:1, R4:1)   P/B add auto label 'Net-(R3-1)'
  step 3 connect_pins(R2:1, R3:1)   joins the two nets the caller built; P line 63 says "two named
                                     nets is a refusal", and both nets are now named by P's own labels
Parts in a row at y 101.6 (pin 1 at y 97.79), x = 101.6, 114.3, 127, 139.7: every route is a
straight wire between neighbours.
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("n01b_autolabel_piecewise", __file__)
from nlib import CP, H, Fixture, dump, show_nets, step  # noqa: E402

fx = Fixture("n01b")
p = fx.path("n01b.kicad_sch")
H.project.create_schematic(p)
for i, x in enumerate((101.6, 114.3, 127, 139.7)):
    H.place(p, "Device:R", f"R{i + 1}", x, 101.6)
focus = [(f"R{i}", "1") for i in range(1, 5)]
show_nets(p, "fixture", focus=focus)
for d in rlib.DESIGNS:
    c = fx.clone(d)
    s = c.path("n01b.kicad_sch")
    for i, (a, b) in enumerate((("R1", "R2"), ("R3", "R4"), ("R2", "R3")), 1):
        step(d, f"N01b connect_pins {a}:1 -> {b}:1", s, [s], "connect_pins",
             CP(a, "1", b, "1", s), pair=((a, "1"), (b, "1")), vid="n01b", stepno=i, nsteps=3)
    show_nets(s, f"[{d}] after the three calls", focus=focus)

dump(__file__)
