"""Rerun of probe-decisions/d02_sheetpin_only_name.py on M2 and K (DEC-02 C, DEC-03 GND/GLOBAL A/B,
DEC-04 plain A/B).

Fixture (dlib.hier_fixture copy): parent R1:1 on the stub of sheet pin SP (parent auto label
removed), child R11:1 on the child's SP net. Child variants plain | GND (power:GND on the child
net) | GLOBAL (global label GEXT on the child net). Parent: R5:1 on local N7; R6 free, in line with
R1:1. Calls (fresh copy each): A wire([R1:1],'N7')  B connect(R1:1, R5:1)  C connect(R1:1, R6:1).
Judged on the root export (rlib copies the whole project directory).
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import dhelp as D  # noqa: E402  (imports rlib first)
from dhelp import C, R, W  # noqa: E402

R.begin("decisions", __file__, tag="dec_d02")
S = "probe-decisions/d02_sheetpin_only_name.py; rerun decisions/dec_d02.py"
RES = "Device:R"


def gnd(kid, pro):
    D.schematic.place_component(lib_id="power:GND", reference="#PWR01", value="GND", x=27.94,
                                y=25.4, schematic_path=kid, project_path=pro)


def glob(kid, pro):
    D.label(kid, "GEXT", 27.94, 25.4, kind="global_label")


CASE = {  # (variant, call) -> (case, also)
    ("plain", "A"): ("DEC-04", ["DEC-03"]), ("plain", "B"): ("DEC-04", ["DEC-03"]),
    ("GND", "A"): ("DEC-03", []), ("GND", "B"): ("DEC-03", []),
    ("GLOBAL", "A"): ("DEC-03", []), ("GLOBAL", "B"): ("DEC-03", []),
    ("plain", "C"): ("DEC-02", ["DEC-03"]), ("GND", "C"): ("DEC-02", ["DEC-03"]),
    ("GLOBAL", "C"): ("DEC-02", ["DEC-03"]),
}
for vname, extra in {"plain": None, "GND": gnd, "GLOBAL": glob}.items():
    root, pro, kid = D.d_hier_fixture(f"d02_{vname}", child_extra=extra)
    D.hplace(root, RES, "R5", 152.4, 101.6)
    D.d_stub_label(root, "R5", "1", "N7")
    D.hplace(root, RES, "R6", 76.2, 57.15)  # R6:1 at (76.2, 53.34), in line with R1:1
    print(f"variant {vname}: R1:1 {D.pos(root, 'R1', '1')} R6:1 {D.pos(root, 'R6', '1')}")
    for call, tool, kw in (("A", "wire_pins_to_net", W([("R1", "1")], "N7")),
                           ("B", "connect_pins", C("R1", "1", "R5", "1")),
                           ("C", "connect_pins", C("R1", "1", "R6", "1"))):
        case, also = CASE[(vname, call)]
        R.run_case(case, f"d02_sheetpin_only_name:{vname}:{call}", tool, root, kw, root=root,
                   script=S, also=also)
R.end()
