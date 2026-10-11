"""Rerun of verify-probe-decisions/v11_units_across.py on M2 and K (DEC-14).

Fixture copied from v11: root 'vp' with sheet 'sub' (no pins). RN1 unit A on the root at
(63.5, 76.2); RN1 unit B on the child at (88.9, 63.5) (top-level and instance unit 2). Child:
RN1:1's copy and R12:1 on local X. Root: R5:1 on N. Call on the root: wire([RN1:1],'N').
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import dhelp as D  # noqa: E402  (imports rlib first)
from dhelp import H, R, W  # noqa: E402

R.begin("decisions", __file__, tag="dec_v11")
S = "verify-probe-decisions/v11_units_across.py; rerun decisions/dec_v11.py"
RES = "Device:R"

d = H.BASE / "v_v11"
D.project.create_project(str(d), "vp")
root, pro, kid = str(d / "vp.kicad_sch"), str(d / "vp.kicad_pro"), str(d / "sub.kicad_sch")
D.project.create_schematic(kid)
D.project.add_hierarchical_sheet(root, "sub", kid, pins=[], x=152.4, y=38.1, project_path=pro)
D.v_place(root, "Device:R_Network03_Split", "RN1", 63.5, 76.2, value="RN", project_path=pro)
D.v_place(kid, "Device:R_Network03_Split", "RN1", 88.9, 63.5, value="RN", project_path=pro)


def unit2(r):
    sym = next(s for s in r.find_all("symbol") if D.schematic._sym_property_cst(s, "Reference") == "RN1")
    sym.find("unit").atoms[1].set_text("2")
    n = 0
    for proj in sym.find("instances").find_all("project"):
        for ip in proj.find_all("path"):
            if ip.find("unit") is not None:
                ip.find("unit").atoms[1].set_text("2")
                n += 1
    assert n


D.v_edit(kid, unit2)
D.v_place(kid, RES, "R12", 114.3, 63.5, project_path=pro)
D.v_stubbed(kid, "RN1", "1", "X")
D.v_stubbed(kid, "R12", "1", "X")
D.v_place(root, RES, "R5", 139.7, 101.6, project_path=pro)
D.v_stubbed(root, "R5", "1", "N")
print("root RN1:1", D.pos(root, "RN1", "1"), "kid RN1:1", D.pos(kid, "RN1", "1"))
R.run_case("DEC-14", "v11_units_across:root", "wire_pins_to_net", root, W([("RN1", "1")], "N"),
           root=root, script=S)
R.end()
