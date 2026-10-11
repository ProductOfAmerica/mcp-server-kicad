# probeB copy of pr57pt/probe-naming/n16_sheetpin_with_parent_label.py: header swapped to the probeB shims, the design loop is M2/K, step() calls carry vid=/stepno=; fixture code unchanged.
"""N16: M refuses wire_pins_to_net whenever the target's component holds a sheet pin, whatever
label_text is (the correction supplied with M). In the repo's default add_hierarchical_sheet
layout the parent stub also carries a local label named after the pin.

Root 'hp' + child 'kid' (sheet 'Kid', pin SDA at (101.6, 53.34)); parent label 'SDA' at the stub
end (99.06, 53.34) is KEPT. R1:1 sits on the stub end. Child: hier SDA + local SDA + R3:1.
  a  wire [R1:1] -> 'SDA'   (KiCad: already on SDA; a no-op is right)
  b  wire [R1:1] -> 'N'     (R1:1 is on the named net SDA; refusing is right)
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("n16_sheetpin_with_parent_label", __file__)
from nlib import BS, Fixture, W, dump, hier_project, pos, show_nets, step  # noqa: E402

for tag, N in (("a", "SDA"), ("b", "N")):
    fx = Fixture(f"n16_{tag}")
    root, pro, kid = hier_project(fx.src, "hp", "kid.kicad_sch", "Kid",
                                  [{"name": "SDA", "direction": "input"}], keep_parent_labels=True)
    BS.place_component(lib_id="Device:R", reference="R3", value="R3", x=27.94, y=29.21,
                       schematic_path=kid, project_path=pro)
    BS.place_component(lib_id="Device:R", reference="R1", value="R1", x=99.06, y=57.15,
                       schematic_path=root, project_path=pro)
    assert pos(root, "R1", "1") == (99.06, 53.34)
    focus = [("R1", "1"), ("R3", "1")]
    show_nets(root, f"{tag} fixture (root export)", focus=focus)
    for d in rlib.DESIGNS:
        c = fx.clone(d)
        r, k = c.path("hp.kicad_sch"), c.path("kid.kicad_sch")
        step(d, f"N16{tag}: wire_pins_to_net [R1:1] -> {N} (R1:1 on the sheet-pin net labelled SDA)",
             r, [r, k], "wire_pins_to_net", W([("R1", "1")], N, r), targets=[("R1", "1")], N=N, vid=tag)
        show_nets(r, f"[{d}] {tag} after", focus=focus)

dump(__file__)
