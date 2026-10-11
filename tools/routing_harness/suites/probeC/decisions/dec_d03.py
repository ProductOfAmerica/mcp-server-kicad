"""Rerun of probe-decisions/d03_N_equals_sheetpin.py on M2 and K (DEC-03 h3/h4, DEC-04 h1/h2,
DEC-05 h5).

Same hierarchy as d02 (parent R1:1 on the stub of sheet pin SP, parent auto label removed, child
R11:1 on the child's SP net). Variants:
  h1 child plain, no other SP item     h2 child plain, R7:1 on a parent local label SP
  h3 child GND, R7:1 on parent SP      h4 child GND, no other SP item
  h5 child plain, the repo's parent auto label SP KEPT (R1:1 on it as well as on the sheet pin)
Call: wire([R1:1], 'SP'). The h5 truth (a raw local label SP on R1:1) is a control, not re-run.
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import dhelp as D  # noqa: E402  (imports rlib first)
from dhelp import H, R, W  # noqa: E402

R.begin("decisions", __file__, tag="dec_d03")
S = "probe-decisions/d03_N_equals_sheetpin.py; rerun decisions/dec_d03.py"
RES = "Device:R"


def gnd(kid, pro):
    D.schematic.place_component(lib_id="power:GND", reference="#PWR01", value="GND", x=27.94,
                                y=25.4, schematic_path=kid, project_path=pro)


VARIANTS = {"h1": (None, False), "h2": (None, True), "h3": (gnd, True), "h4": (gnd, False)}
CASE = {"h1": ("DEC-04", ["DEC-03"]), "h2": ("DEC-04", ["DEC-03"]), "h3": ("DEC-03", []),
        "h4": ("DEC-03", [])}
for vname, (extra, other_sp) in VARIANTS.items():
    root, pro, kid = D.d_hier_fixture(f"d03_{vname}", child_extra=extra)
    D.hplace(root, RES, "R7", 152.4, 101.6)
    if other_sp:
        D.d_stub_label(root, "R7", "1", "SP")
    case, also = CASE[vname]
    R.run_case(case, f"d03_N_equals_sheetpin:{vname}", "wire_pins_to_net", root,
               W([("R1", "1")], "SP"), root=root, script=S, also=also)

# h5: keep the parent auto label (built like hier_fixture, without removing it)
d = H.BASE / "d03_h5"
D.project.create_project(str(d), "hp")
root, pro, kid = str(d / "hp.kicad_sch"), str(d / "hp.kicad_pro"), str(d / "kid.kicad_sch")
D.project.create_schematic(kid)
D.project.add_hierarchical_sheet(root, "kid", kid, pins=[{"name": "SP", "direction": "input"}],
                                 x=101.6, y=50.8, project_path=pro)
D.schematic.place_component(lib_id="Device:R", reference="R11", value="R11", x=27.94, y=29.21,
                            schematic_path=kid, project_path=pro)
D.schematic.place_component(lib_id="Device:R", reference="R1", value="R1", x=99.06, y=57.15,
                            schematic_path=root, project_path=pro)
D.hplace(root, RES, "R7", 152.4, 101.6)
D.d_stub_label(root, "R7", "1", "SP")
R.run_case("DEC-05", "d03_N_equals_sheetpin:h5", "wire_pins_to_net", root, W([("R1", "1")], "SP"),
           root=root, script=S, also=["DEC-03"])
R.end()
