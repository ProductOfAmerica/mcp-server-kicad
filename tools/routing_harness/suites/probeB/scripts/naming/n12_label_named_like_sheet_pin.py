# probeB copy of pr57pt/probe-naming/n12_label_named_like_sheet_pin.py: header swapped to the probeB shims, the design loop is M2/K, step() calls carry vid=/stepno=; fixture code unchanged.
"""N12: wire_pins_to_net on a parent sheet with label_text equal to a sheet pin's name. A sheet pin
is a port, not a name (digest H1): it joins a same-named label on its own sheet only when that
label is a secondary driver. Does the new label reach the port's net, and does it rename it?

Root 'hp' + child 'kid' (sheet 'Kid', pin SDA at (101.6, 53.34)); parent stub label removed.
Parent R1:1 sits on the stub end, so the chain {R1:1, child R3:1} is named only by the port.
Parent R2 (127, 101.6) is free.
  a  child keeps the local 'SDA' label add_hierarchical_sheet writes beside its hier label
  b  child has only the hierarchical label
Call: wire_pins_to_net([R2:1], 'SDA') on the root.
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("n12_label_named_like_sheet_pin", __file__)
from nlib import BS, Fixture, W, dump, hier_project, pos, show_nets, step  # noqa: E402

for variant in ("a", "b"):
    fx = Fixture(f"n12_{variant}")
    root, pro, kid = hier_project(fx.src, "hp", "kid.kicad_sch", "Kid",
                                  [{"name": "SDA", "direction": "input"}],
                                  keep_child_locals=(variant == "a"))
    BS.place_component(lib_id="Device:R", reference="R3", value="R3", x=27.94, y=29.21,
                       schematic_path=kid, project_path=pro)
    BS.place_component(lib_id="Device:R", reference="R1", value="R1", x=99.06, y=57.15,
                       schematic_path=root, project_path=pro)
    BS.place_component(lib_id="Device:R", reference="R2", value="R2", x=127, y=101.6,
                       schematic_path=root, project_path=pro)
    assert pos(root, "R1", "1") == (99.06, 53.34)
    focus = [("R1", "1"), ("R2", "1"), ("R3", "1")]
    show_nets(root, f"{variant} fixture (root export)", focus=focus)
    for d in rlib.DESIGNS:
        c = fx.clone(d)
        r, k = c.path("hp.kicad_sch"), c.path("kid.kicad_sch")
        step(d, f"N12{variant}: on the root, wire_pins_to_net [R2:1] -> SDA", r, [r, k],
             "wire_pins_to_net", W([("R2", "1")], "SDA", r), targets=[("R2", "1")], N="SDA", vid=variant)
        after = show_nets(r, f"[{d}] {variant} after", focus=focus)
        print("    R2:1 joined the port's net (R1:1/R3:1):",
              any(("R2", "1") in v and ("R1", "1") in v for _, _, v in after))

dump(__file__)
