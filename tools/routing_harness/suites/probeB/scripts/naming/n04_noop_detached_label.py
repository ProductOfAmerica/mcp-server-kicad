# probeB copy of pr57pt/probe-naming/n04_noop_detached_label.py: header swapped to the probeB shims, the design loop is M2/K, step() calls carry vid=/stepno=; fixture code unchanged.
"""N04: the 'already on N: no-op' decision (P line 43) when N sits on the target's wire but KiCad 9
does not attach it (digest G12/F07/F08: a second label, a pin end or an NC flag at the label's
anchor on a wire interior stops the wire attach).

R1:1 (101.6, 97.79) is wired right to R5:2 (rot 90 part); R7:1 elsewhere carries local 'N'.
Stacked items sit on that wire's interior at X = (110.49, 97.79).

  a_two_labels : labels 'N' and 'M' both at X        (KiCad 10.0.6+ would attach both, digest G13)
  b_pin_end    : label 'N' at X, where R6:1's pin end also lies (R6 placed below)
  c_nc_flag    : label 'N' and an NC flag at X
Call: wire_pins_to_net([R1:1], 'N').  KiCad 9 truth decides whether R1:1 is already on N.
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("n04_noop_detached_label", __file__)
from nlib import H, Fixture, W, dump, pos, show_nets, step  # noqa: E402

X = (110.49, 97.79)
for variant in ("a_two_labels", "b_pin_end", "c_nc_flag"):
    fx = Fixture(f"n04_{variant}")
    p = fx.path("n04.kicad_sch")
    H.project.create_schematic(p)
    H.place(p, "Device:R", "R1", 101.6, 101.6)
    H.place(p, "Device:R", "R5", 125.73, 97.79, rot=90)
    H.place(p, "Device:R", "R7", 152.4, 127)
    a = pos(p, "R1", "1")
    b5 = {n: pos(p, "R5", n) for n in ("1", "2")}
    near = min(b5.values())
    print("R5 pins", b5, "wired:", near)
    H.wire(p, *a, *near)
    x7, y7 = pos(p, "R7", "1")
    H.wire(p, x7, y7, x7, 120.65)
    H.label(p, "N", x7, 120.65)
    H.label(p, "N", *X)
    focus = [("R1", "1"), ("R5", "1"), ("R5", "2"), ("R7", "1")]
    if variant == "a_two_labels":
        H.label(p, "M", *X)
    elif variant == "b_pin_end":
        H.place(p, "Device:R", "R6", 110.49, 101.6)
        assert pos(p, "R6", "1") == X, pos(p, "R6", "1")
        focus.append(("R6", "1"))
    else:
        H.no_connect(p, *X)
    show_nets(p, f"{variant} fixture", focus=focus)
    for d in rlib.DESIGNS:
        c = fx.clone(d)
        s = c.path("n04.kicad_sch")
        step(d, f"N04{variant}: wire_pins_to_net [R1:1] -> N", s, [s], "wire_pins_to_net",
             W([("R1", "1")], "N", s), targets=[("R1", "1")], N="N", vid=variant)
        show_nets(s, f"[{d}] {variant} after", focus=focus)

dump(__file__)
