"""Rerun of probe-composite/c06_subsheet_power_names.py (CMP-05, also CMP-10) on M2 and K.

Project root carries power:VCC and power:GND on R1; child sheet 'kid'. Cap placed with
power_net='VCC', ground_net='GND', project_path = the project:
  K1 child has no power symbols (cap on the child);
  K2 child also carries power:GND on R8:2 (cap on the child);
  K0 control: the same call on the root sheet.
build() copied verbatim. Judged on the root export by rlib (root= the root sheet).
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib as R  # noqa: E402
from rlib import H, SCH  # noqa: E402

import cmplib as C  # noqa: E402


def build(tag, child_gnd):
    d = H.BASE / tag
    H.project.create_project(str(d), tag)
    root, pro = str(d / f"{tag}.kicad_sch"), str(d / f"{tag}.kicad_pro")
    kid = str(d / "kid.kicad_sch")
    H.project.create_schematic(kid)
    H.project.add_hierarchical_sheet(parent_schematic_path=root, sheet_name="kid", sheet_file=kid,
                                     pins=[], x=152.4, y=25.4, project_path=pro)
    pc = SCH.place_component
    pc(lib_id="Device:R", reference="R1", value="1k", x=50.8, y=101.6, schematic_path=root,
       project_path=pro)                                   # R1:1 (50.8, 97.79), R1:2 (50.8, 105.41)
    H.wire(root, 50.8, 97.79, 50.8, 92.71)
    pc(lib_id="power:VCC", reference="#PWR01", value="VCC", x=50.8, y=92.71, schematic_path=root,
       project_path=pro)
    H.wire(root, 50.8, 105.41, 50.8, 110.49)
    pc(lib_id="power:GND", reference="#PWR02", value="GND", x=50.8, y=110.49,
       schematic_path=root, project_path=pro)
    pc(lib_id="Device:R", reference="R8", value="1k", x=50.8, y=50.8, schematic_path=kid,
       project_path=pro)                                   # R8:2 at (50.8, 54.61)
    if child_gnd:
        H.wire(kid, 50.8, 54.61, 50.8, 59.69)
        pc(lib_id="power:GND", reference="#PWR03", value="GND", x=50.8, y=59.69,
           schematic_path=kid, project_path=pro)
    return {"root": root, "pro": pro, "kid": kid}


R.begin("composite", __file__, tag="cmp_c06_subsheet_power_names")
S = "probe-composite/c06_subsheet_power_names.py"
for case, child_gnd, on_root in (("K1", False, False), ("K2", True, False), ("K0", False, True)):
    fx = build(f"c06_{case}", child_gnd)
    target = fx["root"] if on_root else fx["kid"]
    kw = dict(lib_id="Device:C", reference="C1", value="100n", x=127, y=101.6, power_net="VCC",
              ground_net="GND", project_path=fx["pro"])
    C.run_case("CMP-05", f"c06_subsheet_power_names:{case}", "auto_place_decoupling_cap",
               target, kw, root=fx["root"], script=S, also=["CMP-10"],
               note=("root sheet" if on_root else "child sheet")
               + (" (child has power:GND)" if child_gnd else ""))
R.end()
