"""Rerun of probe-decisions/d11_pad_copies_across_sheets.py on M2 and K (DEC-14).

Fixture copied from d11: RN1 (Device:R_Network03_Split) unit A on the root, unit B on the child
'kid' (top-level and instance unit set to 2); the child's RN1:1 copy and R12:1 on local X; root
R5:1 on N. Call on the root: wire([RN1:1],'N'). Judged on the root export.
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import dhelp as D  # noqa: E402  (imports rlib first)
from dhelp import H, R, W  # noqa: E402

R.begin("decisions", __file__, tag="dec_d11")
S = "probe-decisions/d11_pad_copies_across_sheets.py; rerun decisions/dec_d11.py"

d = H.BASE / "d11"
D.project.create_project(str(d), "hp")
root, pro, kid = str(d / "hp.kicad_sch"), str(d / "hp.kicad_pro"), str(d / "kid.kicad_sch")
D.project.create_schematic(kid)
D.project.add_hierarchical_sheet(root, "kid", kid, pins=[], x=152.4, y=50.8, project_path=pro)
D.schematic.place_component(lib_id="Device:R_Network03_Split", reference="RN1", value="RN",
                            x=101.6, y=101.6, schematic_path=root, project_path=pro)
D.schematic.place_component(lib_id="Device:R_Network03_Split", reference="RN1", value="RN",
                            x=101.6, y=101.6, schematic_path=kid, project_path=pro)
tree = D._cst.parse(open(kid, "rb").read())
r = tree.lists[0]
sym = next(s for s in r.find_all("symbol") if D.schematic._sym_property_cst(s, "Reference") == "RN1")
sym.find("unit").atoms[1].set_text("2")
n = 0
for proj in sym.find("instances").find_all("project"):
    for path in proj.find_all("path"):
        u = path.find("unit")
        if u is not None:
            u.atoms[1].set_text("2")
            n += 1
assert n
open(kid, "wb").write(D._cst.serialize(tree))
D.schematic.place_component(lib_id="Device:R", reference="R12", value="R12", x=152.4, y=101.6,
                            schematic_path=kid, project_path=pro)
D.d_stub_label(kid, "RN1", "1", "X")
D.d_stub_label(kid, "R12", "1", "X")
D.schematic.place_component(lib_id="Device:R", reference="R5", value="R5", x=177.8, y=101.6,
                            schematic_path=root, project_path=pro)
D.d_stub_label(root, "R5", "1", "N")
print("root RN1 copies:", R.M2.pin_positions(root, "RN1"))
print("kid  RN1 copies:", R.M2.pin_positions(kid, "RN1"))
R.run_case("DEC-14", "d11_pad_copies_across_sheets:root", "wire_pins_to_net", root,
           W([("RN1", "1")], "N"), root=root, script=S)
R.end()
