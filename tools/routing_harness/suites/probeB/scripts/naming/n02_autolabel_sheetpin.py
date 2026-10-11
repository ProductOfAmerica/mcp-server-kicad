# probeB copy of pr57pt/probe-naming/n02_autolabel_sheetpin.py: header swapped to the probeB shims, the design loop is M2/K, step() calls carry vid=/stepno=; fixture code unchanged.
"""N02: connect_pins' auto label (P, B) and wire_pins_to_net on a parent net that is named only
through a sheet pin (no label on the parent side).

Root 'hp' + child 'kid.kicad_sch' (sheet 'Kid', pin SDA at (101.6, 53.34)); the parent stub label
that add_hierarchical_sheet writes is removed, so the parent subgraph {R1:1, stub, sheet pin}
carries no label. Child: hier label SDA + local SDA + R3:1.
R1:1 sits on the parent stub end (99.06, 53.34); R2:1 at (88.9, 53.34) is aligned (straight route).

variants
  plain  : nothing else.                         call connect_pins(R1:1, R2:1)
  promoC : R4:1's net carries local AAA + SDA     call connect_pins(R1:1, R2:1)
           (sheet pin SDA is promoted and joins R4 through R4's secondary SDA, digest H1-refuted)
  promoW : same fixture as promoC                 call wire_pins_to_net([R1:1], 'N')
  promoS : same fixture as promoC                 call wire_pins_to_net([R1:1], 'SDA')
  gnd    : child SDA subgraph also has power GND; R2:1 carries local SIG.
                                                  call connect_pins(R1:1, R2:1)
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("n02_autolabel_sheetpin", __file__)
from nlib import BS, CP, H, Fixture, W, dump, hier_project, pos, show_nets, step  # noqa: E402


def build(fx, variant):
    root, pro, kid = hier_project(fx.src, "hp", "kid.kicad_sch", "Kid",
                                  [{"name": "SDA", "direction": "input"}])
    BS.place_component(lib_id="Device:R", reference="R3", value="R3", x=27.94, y=29.21,
                       schematic_path=kid, project_path=pro)
    if variant == "gnd":
        H.wire(kid, 25.4, 25.4, 25.4, 20.32)
        BS.place_component(lib_id="power:GND", reference="#PWR01", value="GND", x=25.4,
                           y=20.32, schematic_path=kid, project_path=pro)
    BS.place_component(lib_id="Device:R", reference="R1", value="R1", x=99.06, y=57.15,
                       schematic_path=root, project_path=pro)
    BS.place_component(lib_id="Device:R", reference="R2", value="R2", x=88.9, y=57.15,
                       schematic_path=root, project_path=pro)
    if variant == "gnd":
        H.wire(root, 88.9, 53.34, 88.9, 50.8)
        H.label(root, "SIG", 88.9, 50.8)
    if variant.startswith("promo"):
        BS.place_component(lib_id="Device:R", reference="R4", value="R4", x=63.5, y=57.15,
                           schematic_path=root, project_path=pro)
        x4, y4 = pos(root, "R4", "1")
        H.wire(root, x4, y4, x4, 48.26)
        H.label(root, "AAA", x4, 48.26)
        H.wire(root, x4, 48.26, 68.58, 48.26)
        H.label(root, "SDA", 68.58, 48.26)
    assert pos(root, "R1", "1") == (99.06, 53.34), pos(root, "R1", "1")
    assert pos(kid, "R3", "1") == (27.94, 25.4), pos(kid, "R3", "1")
    return root, kid


for variant in ("plain", "promoC", "promoW", "promoS", "gnd"):
    fx = Fixture(f"n02_{variant}")
    root, kid = build(fx, variant)
    show_nets(root, f"{variant} fixture (root export)")
    for d in rlib.DESIGNS:
        c = fx.clone(d)
        r, k = c.path("hp.kicad_sch"), c.path("kid.kicad_sch")
        if variant in ("plain", "promoC", "gnd"):
            step(d, f"N02{variant}: connect_pins R1:1 -> R2:1", r, [r, k], "connect_pins",
                 CP("R1", "1", "R2", "1", r), pair=(("R1", "1"), ("R2", "1")), vid=variant)
        elif variant == "promoW":
            step(d, f"N02{variant}: wire_pins_to_net [R1:1] -> N", r, [r, k], "wire_pins_to_net",
                 W([("R1", "1")], "N", r), targets=[("R1", "1")], N="N", vid=variant)
        else:
            step(d, f"N02{variant}: wire_pins_to_net [R1:1] -> SDA", r, [r, k],
                 "wire_pins_to_net", W([("R1", "1")], "SDA", r), targets=[("R1", "1")], N="SDA", vid=variant)
        show_nets(r, f"[{d}] {variant} after",
                  focus=[("R1", "1"), ("R2", "1"), ("R3", "1"), ("R4", "1")])

dump(__file__)
