# probeB copy of pr57pt/probe-naming/n03_autolabel_midwire_hier.py: header swapped to the probeB shims, the design loop is M2/K, step() calls carry vid=/stepno=; fixture code unchanged.
"""N03: connect_pins' auto 'Net-(R1-1)' label against names P's model might miss.

R1:1 (101.6, 97.79) is wired up to R5:2 (101.6, 87.63). R2:1 (127, 97.79) is free and aligned,
so connect_pins(R1:1, R2:1) is one straight wire.

  a_exact : local label 'SIG' on the wire interior at (101.6, 92.71)       (P rule 3 sees it)
  b_1iu   : local label 'SIG' at (101.6001, 92.71), 1 IU off the wire      (KiCad attaches, digest
            G10/LABEL-SLACK; P's exact on-segment test does not)
  b_1iu_W : same fixture, wire_pins_to_net([R1:1], 'N') instead of connect_pins
  c_kid   : child sheet: R1:1's net carries hierarchical label 'H' (port of a sheet-pin-only
            parent net), connect_pins in the child
  d_root  : root sheet: R1:1's net carries a hierarchical label 'H' on the wire end (root hier
            label acts as a local name, digest N08)
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("n03_autolabel_midwire_hier", __file__)
from nlib import BS, CP, H, Fixture, W, dump, hier_project, pos, show_nets, step  # noqa: E402


def base(p):
    H.place(p, "Device:R", "R1", 101.6, 101.6)
    H.place(p, "Device:R", "R2", 127, 101.6)
    H.place(p, "Device:R", "R5", 101.6, 83.82)
    a, b = pos(p, "R1", "1"), pos(p, "R5", "2")
    assert a == (101.6, 97.79) and b == (101.6, 87.63), (a, b)
    H.wire(p, *a, *b)


focus = [("R1", "1"), ("R2", "1"), ("R5", "2")]
for variant in ("a_exact", "b_1iu", "b_1iu_W", "d_root"):
    fx = Fixture(f"n03_{variant}")
    p = fx.path("n03.kicad_sch")
    H.project.create_schematic(p)
    base(p)
    if variant == "a_exact":
        H.label(p, "SIG", 101.6, 92.71)
    elif variant.startswith("b_1iu"):
        H.label(p, "SIG", 101.6001, 92.71)
    elif variant == "d_root":
        H.label(p, "H", 101.6, 87.63, kind="hierarchical_label")
    show_nets(p, f"{variant} fixture", focus=focus)
    for d in rlib.DESIGNS:
        c = fx.clone(d)
        s = c.path("n03.kicad_sch")
        if variant == "b_1iu_W":
            step(d, f"N03{variant}: wire_pins_to_net [R1:1] -> N", s, [s], "wire_pins_to_net",
                 W([("R1", "1")], "N", s), targets=[("R1", "1")], N="N", vid=variant)
        else:
            step(d, f"N03{variant}: connect_pins R1:1 -> R2:1", s, [s], "connect_pins",
                 CP("R1", "1", "R2", "1", s), pair=(("R1", "1"), ("R2", "1")), vid=variant)
        show_nets(s, f"[{d}] {variant} after", focus=focus)

# c_kid: the same geometry inside a child sheet whose parent net is a bare sheet pin
fx = Fixture("n03_c_kid")
root, pro, kid = hier_project(fx.src, "hp", "kid.kicad_sch", "Kid",
                              [{"name": "H", "direction": "input"}], keep_child_locals=False)
for ref, x, y in (("R1", 101.6, 101.6), ("R2", 127, 101.6), ("R5", 101.6, 83.82)):
    BS.place_component(lib_id="Device:R", reference=ref, value=ref, x=x, y=y,
                       schematic_path=kid, project_path=pro)
H.wire(kid, 101.6, 97.79, 101.6, 87.63)
# the child's hier label H (written by add_hierarchical_sheet at (25.4, 25.4), stub to 27.94) is
# wired to R5:2's end (101.6, 87.63) around the parts: right, down, left (end-to-end contacts only)
H.wire(kid, 27.94, 25.4, 114.3, 25.4)
H.wire(kid, 114.3, 25.4, 114.3, 87.63)
H.wire(kid, 114.3, 87.63, 101.6, 87.63)
BS.place_component(lib_id="Device:R", reference="R9", value="R9", x=50.8, y=101.6,
                   schematic_path=root, project_path=pro)
H.wire(root, 99.06, 53.34, 50.8, 53.34)  # parent stub end -> R9 pin 1 column
H.wire(root, 50.8, 53.34, 50.8, 97.79)
show_nets(root, "c_kid fixture (root export)",
          focus=[("R1", "1"), ("R2", "1"), ("R5", "2"), ("R9", "1")])
for d in rlib.DESIGNS:
    c = fx.clone(d)
    r, k = c.path("hp.kicad_sch"), c.path("kid.kicad_sch")
    step(d, "N03c_kid: connect_pins R1:1 -> R2:1 in the child", r, [r, k], "connect_pins",
         CP("R1", "1", "R2", "1", k), pair=(("R1", "1"), ("R2", "1")), vid="c_kid")
    show_nets(r, f"[{d}] c_kid after", focus=[("R1", "1"), ("R2", "1"), ("R5", "2"), ("R9", "1")])

dump(__file__)
