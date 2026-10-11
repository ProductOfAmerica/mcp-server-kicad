"""Rerun of probe-decisions/d16_N_global_elsewhere_only.py on M2 and K (DEC-20).

Fixture copied from d16: root power:GND wired to R1:1, global label GG on R2:1; child 'kid' with R11
and R12 free and no GND symbol or GG label.
  a child wire([R11:1],'GND')   b child wire([R12:1],'GG')
Edited file: the child; judged on the root export.
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import dhelp as D  # noqa: E402  (imports rlib first)
from dhelp import H, R, W  # noqa: E402

R.begin("decisions", __file__, tag="dec_d16")
S = "probe-decisions/d16_N_global_elsewhere_only.py; rerun decisions/dec_d16.py"

d = H.BASE / "d16"
D.project.create_project(str(d), "hp")
root, pro, kid = str(d / "hp.kicad_sch"), str(d / "hp.kicad_pro"), str(d / "kid.kicad_sch")
D.project.create_schematic(kid)
D.project.add_hierarchical_sheet(root, "kid", kid, pins=[], x=152.4, y=50.8, project_path=pro)
for r, x in (("R1", 101.6), ("R2", 127)):
    D.schematic.place_component(lib_id="Device:R", reference=r, value=r, x=x, y=101.6,
                                schematic_path=root, project_path=pro)
D.schematic.place_component(lib_id="power:GND", reference="#PWR01", value="GND", x=101.6, y=91.44,
                            rotation=180, schematic_path=root, project_path=pro)
D.wire(root, *D.pos(root, "#PWR01", "1"), *D.pos(root, "R1", "1"))
D.d_stub_label(root, "R2", "1", "GG", kind="global_label")
for r, x in (("R11", 101.6), ("R12", 127)):
    D.schematic.place_component(lib_id="Device:R", reference=r, value=r, x=x, y=101.6,
                                schematic_path=kid, project_path=pro)
for tag, tgt, N in (("a", ("R11", "1"), "GND"), ("b", ("R12", "1"), "GG")):
    R.run_case("DEC-20", f"d16_N_global_elsewhere_only:{tag}", "wire_pins_to_net", kid, W([tgt], N),
               root=root, script=S)
R.end()
