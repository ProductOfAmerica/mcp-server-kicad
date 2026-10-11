# probeB copy of pr57pt/probe-naming/n02b_wire_sheetpin_chain.py: header swapped to the probeB shims, the design loop is M2/K, step() calls carry vid=/stepno=; fixture code unchanged.
"""N02b: wire_pins_to_net on a parent net named only through a sheet pin (n02 'plain' fixture:
chain '/Kid/SDA' = {parent R1:1, child R3:1}, no parent label).

  s  wire [R1:1] -> 'SDA'   the port's own name
  n  wire [R1:1] -> 'N'     a new name (the builder's O7 child-plain is the same shape)
P treats a sheet pin as no name (builder I08), so R1:1 is "unnamed" and P proceeds.
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("n02b_wire_sheetpin_chain", __file__)
from nlib import BS, Fixture, W, dump, hier_project, pos, show_nets, step  # noqa: E402

for tag, N in (("s", "SDA"), ("n", "N")):
    fx = Fixture(f"n02b_{tag}")
    root, pro, kid = hier_project(fx.src, "hp", "kid.kicad_sch", "Kid",
                                  [{"name": "SDA", "direction": "input"}])
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
        step(d, f"N02b{tag}: wire_pins_to_net [R1:1] -> {N} (R1:1's net named only by the port)",
             r, [r, k], "wire_pins_to_net", W([("R1", "1")], N, r), targets=[("R1", "1")], N=N, vid=tag)
        show_nets(r, f"[{d}] {tag} after", focus=focus)

dump(__file__)
