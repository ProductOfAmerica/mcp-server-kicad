# probeB copy of pr57pt/probe-naming/n03b_1iu_label_isolated.py: header swapped to the probeB shims, the design loop is M2/K, step() calls carry vid=/stepno=; fixture code unchanged.
"""N03b: a label 1 IU off a wire names the net in KiCad 9 (digest G10, LABEL-SLACK), but P's model
uses exact on-segment tests (P line 24, builder I04). Isolated geometry (no stub overlap).

R1:1 (101.6, 97.79) is wired LEFT to R5:2 (rot 90 part); label 'SIG' sits 1 IU above that
horizontal wire's interior at (95.25, 97.7899). R7:1 elsewhere carries local label 'N'.
R1:1's auto stub goes up, away from the wire.

  W : wire_pins_to_net([R1:1], 'N')   R1:1 is on SIG in KiCad, so P line 40 says refuse
  C : connect_pins(R1:1, R2:1)        R2:1 (127, 97.79) free and aligned to the right
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("n03b_1iu_label_isolated", __file__)
from nlib import CP, H, Fixture, W, dump, pos, show_nets, step  # noqa: E402

focus = [("R1", "1"), ("R2", "1"), ("R5", "2"), ("R7", "1")]
for variant in ("W", "C"):
    fx = Fixture(f"n03b_{variant}")
    p = fx.path("n03b.kicad_sch")
    H.project.create_schematic(p)
    H.place(p, "Device:R", "R1", 101.6, 101.6)
    H.place(p, "Device:R", "R2", 127, 101.6)
    H.place(p, "Device:R", "R5", 82.55, 97.79, rot=90)
    H.place(p, "Device:R", "R7", 152.4, 127)
    a = pos(p, "R1", "1")
    b5 = {n: pos(p, "R5", n) for n in ("1", "2")}
    right = max(b5.values())  # R5 pin end nearest R1
    num5 = [n for n, v in b5.items() if v == right][0]
    print("R5 pins", b5, "-> wiring R5:" + num5)
    H.wire(p, *a, *right)
    H.label(p, "SIG", 95.25, 97.7899)
    x7, y7 = pos(p, "R7", "1")
    H.wire(p, x7, y7, x7, 120.65)
    H.label(p, "N", x7, 120.65)
    show_nets(p, f"{variant} fixture", focus=focus + [("R5", "1")])
    for d in rlib.DESIGNS:
        c = fx.clone(d)
        s = c.path("n03b.kicad_sch")
        if variant == "W":
            step(d, "N03b W: wire_pins_to_net [R1:1] -> N (R1:1 on SIG via a 1 IU-off label)",
                 s, [s], "wire_pins_to_net", W([("R1", "1")], "N", s),
                 targets=[("R1", "1")], N="N", vid=variant)
        else:
            step(d, "N03b C: connect_pins R1:1 -> R2:1 (R1:1 on SIG via a 1 IU-off label)",
                 s, [s], "connect_pins", CP("R1", "1", "R2", "1", s),
                 pair=(("R1", "1"), ("R2", "1")), vid=variant)
        show_nets(s, f"[{d}] {variant} after", focus=focus + [("R5", "1")])

dump(__file__)
