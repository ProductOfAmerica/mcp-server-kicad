"""Rerun of probe-decisions/d10_pad_drawn_by_two_units.py on M2 and K (DEC-11 ii/iv, DEC-12 i/v,
DEC-12 and DEC-13 iii).

Fixture copied from d10: Device:R_Network03_Split RN1 unit A (101.6, 101.6) and unit B (127, 101.6)
(add_unit); pin 1 is a unit-0 pad drawn by both, copies at (101.6, 97.79) [A] and (127, 97.79) [B].
R5:1 on N, R6:1 on X, R7:1 (76.2, 97.79) in line with copy A.
  i   both copies free; wire([RN1:1],'N')          ii  copy B on X; wire([RN1:1],'N')
  iii copy A on X, copy B free; wire([RN1:1],'X')  iv  copy A on N, copy B on X; wire([RN1:1],'N')
  v   both copies free; connect(RN1:1, R7:1)
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import dhelp as D  # noqa: E402  (imports rlib first)
from dhelp import C, R, W  # noqa: E402

R.begin("decisions", __file__, tag="dec_d10")
S = "probe-decisions/d10_pad_drawn_by_two_units.py; rerun decisions/dec_d10.py"
RES = "Device:R"


def fixture(name, a=None, b=None):
    p = D.fresh(name)
    D.hplace(p, "Device:R_Network03_Split", "RN1", 101.6, 101.6)
    D.d_add_unit(p, "RN1", 2, 127, 101.6)
    D.hplace(p, RES, "R5", 177.8, 101.6)
    D.d_stub_label(p, "R5", "1", "N")
    D.hplace(p, RES, "R6", 203.2, 101.6)
    D.d_stub_label(p, "R6", "1", "X")
    D.hplace(p, RES, "R7", 76.2, 101.6)       # R7:1 (76.2, 97.79), in line with copy A
    if a:
        D.d_stub_label(p, "RN1", "1", a, i=0)
    if b:
        D.d_stub_label(p, "RN1", "1", b, i=1)
    return p


F = [
    ("i", "DEC-12", [], fixture("d10_i"), "wire", "N"),
    ("ii", "DEC-11", [], fixture("d10_ii", b="X"), "wire", "N"),
    ("iii", "DEC-12", ["DEC-13"], fixture("d10_iii", a="X"), "wire", "X"),
    ("iv", "DEC-11", [], fixture("d10_iv", a="N", b="X"), "wire", "N"),
    ("v", "DEC-12", [], fixture("d10_v"), "connect", None),
]
print("RN1:1 copies:", [D.pos(F[0][3], "RN1", "1", i) for i in (0, 1)])
for tag, case, also, f, kind, N in F:
    if kind == "wire":
        R.run_case(case, f"d10_pad_drawn_by_two_units:{tag}", "wire_pins_to_net", f,
                   W([("RN1", "1")], N), script=S, also=also)
    else:
        R.run_case(case, f"d10_pad_drawn_by_two_units:{tag}", "connect_pins", f,
                   C("RN1", "1", "R7", "1"), script=S, also=also)
R.end()
