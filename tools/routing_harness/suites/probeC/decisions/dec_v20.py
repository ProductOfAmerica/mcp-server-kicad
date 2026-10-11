"""Rerun of verify-probe-decisions/v20_global_elsewhere.py on M2 and K (DEC-20).

Fixture copied from v20: root 'vp' with power:GND wired to R1:1, global label GG on R2:1's stub,
sheet 'sub' (no pins; R11, R12 free, no GND, no GG) and sheet 'port' with sheet pin PB whose
parent stub label was removed (R8:1 at the parent stub end, R21:1 at the child stub end); R9 free
on the root.
  a child(sub) wire([R11:1],'GND')   b child(sub) wire([R12:1],'GG')   c root wire([R9:1],'PB')
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import dhelp as D  # noqa: E402  (imports rlib first)
from dhelp import H, R, W  # noqa: E402

R.begin("decisions", __file__, tag="dec_v20")
S = "verify-probe-decisions/v20_global_elsewhere.py; rerun decisions/dec_v20.py"
RES = "Device:R"

d = H.BASE / "v_v20"
D.project.create_project(str(d), "vp")
root, pro = str(d / "vp.kicad_sch"), str(d / "vp.kicad_pro")
kid, kid2 = str(d / "sub.kicad_sch"), str(d / "port.kicad_sch")
D.project.create_schematic(kid)
D.project.create_schematic(kid2)
D.project.add_hierarchical_sheet(root, "sub", kid, pins=[], x=177.8, y=38.1, project_path=pro)
D.project.add_hierarchical_sheet(root, "port", kid2, pins=[{"name": "PB", "direction": "input"}],
                                 x=177.8, y=88.9, project_path=pro)
assert D.v_remove(root, "label", lambda n: n.atoms[1].text == "PB") == 1
D.v_place(root, RES, "R1", 63.5, 63.5, project_path=pro)
D.v_place(root, RES, "R2", 88.9, 63.5, project_path=pro)
D.v_place(root, "power:GND", "#PWR01", 63.5, 50.8, rot=180, value="GND", project_path=pro)
D.wire(root, *D.pos(root, "#PWR01", "1"), *D.pos(root, "R1", "1"))
D.v_stubbed(root, "R2", "1", "GG", kind="global_label")
D.v_place(kid, RES, "R11", 63.5, 63.5, project_path=pro)
D.v_place(kid, RES, "R12", 88.9, 63.5, project_path=pro)
D.v_place(root, RES, "R8", 175.26, 95.25, project_path=pro)    # R8:1 at the parent stub end
D.v_place(kid2, RES, "R21", 27.94, 29.21, project_path=pro)    # R21:1 at the child stub end
D.v_place(root, RES, "R9", 139.7, 127, project_path=pro)       # free parent pin
print("R8:1", D.pos(root, "R8", "1"), "R21:1", D.pos(kid2, "R21", "1"))
for tag, tgt, N, edited in (("a", ("R11", "1"), "GND", kid), ("b", ("R12", "1"), "GG", kid),
                            ("c", ("R9", "1"), "PB", root)):
    R.run_case("DEC-20", f"v20_global_elsewhere:{tag}", "wire_pins_to_net", edited, W([tgt], N),
               root=root, script=S)
R.end()
