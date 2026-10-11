"""Rerun of verify-probe-decisions/v10_units.py on M2 and K (DEC-11 ii/iv, DEC-12 i/v/vi, DEC-12 and
DEC-13 iii).

Fixture copied from v10: RN1 (Device:R_Network03_Split) unit A at (63.5, 76.2), unit B at
(88.9, 76.2); pin 1 copies at (63.5, 72.39) [A] and (88.9, 72.39) [B]. R5:1 on N, R6:1 on X,
R7:1 (38.1, 72.39) in line with copy A.
  i both free wire([RN1:1],'N')     ii copy B on X wire([RN1:1],'N')
  iii copy A on X wire([RN1:1],'X') iv copy A on N, copy B on X wire([RN1:1],'N')
  v both free connect(RN1:1, R7:1)  vi both free wire([RN1:1, RN1:1],'N')
The literal-M evaluation of iii is an emulator, not re-run.
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import dhelp as D  # noqa: E402  (imports rlib first)
from dhelp import C, R, W  # noqa: E402

R.begin("decisions", __file__, tag="dec_v10")
S = "verify-probe-decisions/v10_units.py; rerun decisions/dec_v10.py"
RES = "Device:R"


def fixture(name, a=None, b=None):
    p = D.v_new(name)
    D.v_place(p, "Device:R_Network03_Split", "RN1", 63.5, 76.2)
    D.v_add_unit(p, "RN1", 2, 88.9, 76.2)
    D.v_place(p, RES, "R5", 139.7, 101.6)
    D.v_stubbed(p, "R5", "1", "N")
    D.v_place(p, RES, "R6", 165.1, 101.6)
    D.v_stubbed(p, "R6", "1", "X")
    D.v_place(p, RES, "R7", 38.1, 76.2)
    if a:
        D.v_stubbed(p, "RN1", "1", a, i=0)
    if b:
        D.v_stubbed(p, "RN1", "1", b, i=1)
    return p


F = [
    ("i", "DEC-12", [], fixture("v10i"), "wire", [("RN1", "1")], "N"),
    ("ii", "DEC-11", [], fixture("v10ii", b="X"), "wire", [("RN1", "1")], "N"),
    ("iii", "DEC-12", ["DEC-13"], fixture("v10iii", a="X"), "wire", [("RN1", "1")], "X"),
    ("iv", "DEC-11", [], fixture("v10iv", a="N", b="X"), "wire", [("RN1", "1")], "N"),
    ("v", "DEC-12", [], fixture("v10v"), "connect", None, None),
    ("vi", "DEC-12", [], fixture("v10vi"), "wire", [("RN1", "1"), ("RN1", "1")], "N"),
]
print("RN1:1 copies:", [D.pos(F[0][3], "RN1", "1", i) for i in (0, 1)],
      "R7:1", D.pos(F[0][3], "R7", "1"))
for tag, case, also, f, kind, pl, N in F:
    if kind == "wire":
        R.run_case(case, f"v10_units:{tag}", "wire_pins_to_net", f, W(pl, N), script=S, also=also)
    else:
        R.run_case(case, f"v10_units:{tag}", "connect_pins", f, C("RN1", "1", "R7", "1"),
                   script=S, also=also)
R.end()
