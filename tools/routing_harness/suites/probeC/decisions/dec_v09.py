"""Rerun of verify-probe-decisions/v09_nc_type.py on M2 and K (DEC-09 a/b, DEC-10 c).

Fixture A (copied): VNC U1:2 (68.58, 63.5) NC-type; R6:1 (88.9, 63.5) free, in line; R5:1 on N.
  a wire([U1:2],'N')   b connect(U1:2, R6:1)   (the raw-label truth is a control, not re-run)
Fixture C: R1:1 (78.74, 63.5) wired left to U1:2's end; R2:1 (68.58, 72.39) wired up to it; R5:1
on N2.   c wire([R1:1],'N2')
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import dhelp as D  # noqa: E402  (imports rlib first)
from dhelp import C, H, R, W  # noqa: E402

R.begin("decisions", __file__, tag="dec_v09")
S = "verify-probe-decisions/v09_nc_type.py; rerun decisions/dec_v09.py"
RES = "Device:R"

lib = D.v_write_lib(H.BASE / "v09.kicad_sym")
a = D.v_new("v09a")
D.v_place_lib(a, lib, "VNC", "U1", 63.5, 63.5)
D.v_place(a, RES, "R6", 88.9, 67.31)
D.v_place(a, RES, "R5", 139.7, 101.6)
D.v_stubbed(a, "R5", "1", "N")
print("U1:2", D.pos(a, "U1", "2"), "R6:1", D.pos(a, "R6", "1"))
R.run_case("DEC-09", "v09_nc_type:a", "wire_pins_to_net", a, W([("U1", "2")], "N"), script=S)
R.run_case("DEC-09", "v09_nc_type:b", "connect_pins", a, C("U1", "2", "R6", "1"), script=S)

cfix = D.v_new("v09c")
D.v_place_lib(cfix, lib, "VNC", "U1", 63.5, 63.5)
D.v_place(cfix, RES, "R1", 78.74, 67.31)
D.v_place(cfix, RES, "R2", 68.58, 76.2)
D.wire(cfix, *D.pos(cfix, "R1", "1"), *D.pos(cfix, "U1", "2"))
D.wire(cfix, *D.pos(cfix, "R2", "1"), *D.pos(cfix, "U1", "2"))
D.v_place(cfix, RES, "R5", 139.7, 101.6)
D.v_stubbed(cfix, "R5", "1", "N2")
print("fixture C: R1:1", D.pos(cfix, "R1", "1"), "R2:1", D.pos(cfix, "R2", "1"))
R.run_case("DEC-10", "v09_nc_type:c", "wire_pins_to_net", cfix, W([("R1", "1")], "N2"), script=S)
R.end()
