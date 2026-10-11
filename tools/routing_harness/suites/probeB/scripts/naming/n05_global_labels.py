# probeB copy of pr57pt/probe-naming/n05_global_labels.py: header swapped to the probeB shims, the design loop is M2/K, step() calls carry vid=/stepno=; fixture code unchanged.
"""N05: global labels and cross-sheet names (item 4).

Single sheet (R1:1 at (101.6, 97.79), auto stub up; R7:1 carries local 'N'):
  a_target_global : R1:1's net carries global label 'G' (stub end).  wire [R1:1] -> 'N'
  b_global_is_N   : R1:1 free; R2:1's net carries global label 'N' (instead of R7's local).
                    wire [R1:1] -> 'N'  (requested join with the global N)
Hierarchy (root 'hp', child 'kid', sheet 'Kid' with pin 'X' wired to nothing of interest):
  c_child_global  : global label 'G' only in the child, on R3:1.  On the root: wire [R1:1] -> 'G'
  d_child_power   : power:GND (Value GND) only in the child, on R3:1. On the root: wire [R1:1] -> 'GND'
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("n05_global_labels", __file__)
from nlib import BS, H, Fixture, W, dump, hier_project, pos, show_nets, step  # noqa: E402

for variant in ("a_target_global", "b_global_is_N"):
    fx = Fixture(f"n05_{variant}")
    p = fx.path("n05.kicad_sch")
    H.project.create_schematic(p)
    H.place(p, "Device:R", "R1", 101.6, 101.6)
    H.place(p, "Device:R", "R2", 127, 101.6)
    H.place(p, "Device:R", "R7", 152.4, 127)
    focus = [("R1", "1"), ("R2", "1"), ("R7", "1")]
    if variant == "a_target_global":
        x1, y1 = pos(p, "R1", "1")
        H.wire(p, x1, y1, 93.98, y1)  # left, so P's up-stub does not overlap it
        H.label(p, "G", 93.98, y1, kind="global_label")
        x7, y7 = pos(p, "R7", "1")
        H.wire(p, x7, y7, x7, 120.65)
        H.label(p, "N", x7, 120.65)
    else:
        x2, y2 = pos(p, "R2", "1")
        H.wire(p, x2, y2, x2, 93.98)
        H.label(p, "N", x2, 93.98, kind="global_label")
    show_nets(p, f"{variant} fixture", focus=focus)
    for d in rlib.DESIGNS:
        c = fx.clone(d)
        s = c.path("n05.kicad_sch")
        step(d, f"N05{variant}: wire_pins_to_net [R1:1] -> N", s, [s], "wire_pins_to_net",
             W([("R1", "1")], "N", s), targets=[("R1", "1")], N="N", vid=variant)
        show_nets(s, f"[{d}] {variant} after", focus=focus)

for variant, N in (("c_child_global", "G"), ("d_child_power", "GND")):
    fx = Fixture(f"n05_{variant}")
    root, pro, kid = hier_project(fx.src, "hp", "kid.kicad_sch", "Kid",
                                  [{"name": "X", "direction": "input"}])
    BS.place_component(lib_id="Device:R", reference="R3", value="R3", x=76.2, y=76.2,
                       schematic_path=kid, project_path=pro)
    x3, y3 = pos(kid, "R3", "1")
    H.wire(kid, x3, y3, x3, 66.04)
    if N == "G":
        H.label(kid, "G", x3, 66.04, kind="global_label")
    else:
        BS.place_component(lib_id="power:GND", reference="#PWR01", value="GND", x=x3, y=66.04,
                           schematic_path=kid, project_path=pro)
    BS.place_component(lib_id="Device:R", reference="R1", value="R1", x=101.6, y=101.6,
                       schematic_path=root, project_path=pro)
    focus = [("R1", "1"), ("R3", "1")]
    show_nets(root, f"{variant} fixture (root export)", focus=focus)
    for d in rlib.DESIGNS:
        c = fx.clone(d)
        r, k = c.path("hp.kicad_sch"), c.path("kid.kicad_sch")
        step(d, f"N05{variant}: on the root, wire_pins_to_net [R1:1] -> {N}", r, [r, k],
             "wire_pins_to_net", W([("R1", "1")], N, r), targets=[("R1", "1")], N=N,
             note=f"'{N}' exists only in the child sheet", vid=variant)
        after = show_nets(r, f"[{d}] {variant} after", focus=focus)
        same = any(("R1", "1") in v and ("R3", "1") in v for _, _, v in after)
        print(f"    R1:1 joined the child's '{N}' net (R3:1): {same}")

dump(__file__)
