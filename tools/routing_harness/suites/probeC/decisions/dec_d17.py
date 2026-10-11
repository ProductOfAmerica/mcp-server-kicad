"""Rerun of probe-decisions/d17_basic_decisions.py on M2 and K (DEC-25).

Fixtures copied from d17 (R1:1 at (101.6, 97.79) up; R5:1 on B):
  1 R1:1 on local label A        2 R1:1 wired to R2:1 (unnamed)    3 R1:1 on local label B
  4 NC flag on R1:1              5 R1:1 wired to R2:1, foreign labels X95/X92/X90 on the 1x/2x/3x
                                   stub ends
Call: wire([R1:1],'B').
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import dhelp as D  # noqa: E402  (imports rlib first)
from dhelp import R, W  # noqa: E402

R.begin("decisions", __file__, tag="dec_d17")
S = "probe-decisions/d17_basic_decisions.py; rerun decisions/dec_d17.py"
RES = "Device:R"


def base(name):
    p = D.fresh(name)
    D.hplace(p, RES, "R1", 101.6, 101.6)     # R1:1 (101.6, 97.79) up
    D.hplace(p, RES, "R5", 177.8, 101.6)
    D.d_stub_label(p, "R5", "1", "B")
    return p


f1 = base("d17_1")
D.d_stub_label(f1, "R1", "1", "A")
f2 = base("d17_2")
D.hplace(f2, RES, "R2", 127, 101.6)
D.wire(f2, *D.pos(f2, "R1", "1"), *D.pos(f2, "R2", "1"))
f3 = base("d17_3")
D.d_stub_label(f3, "R1", "1", "B")
f4 = base("d17_4")
D.no_connect(f4, *D.pos(f4, "R1", "1"))
f5 = base("d17_5")
D.hplace(f5, RES, "R2", 127, 101.6)
D.wire(f5, *D.pos(f5, "R1", "1"), *D.pos(f5, "R2", "1"))
for y in (95.25, 92.71, 90.17):          # 1x, 2x, 3x stub ends hold foreign labels
    D.label(f5, f"X{int(y)}", 101.6, y)

for tag, f in (("1", f1), ("2", f2), ("3", f3), ("4", f4), ("5", f5)):
    R.run_case("DEC-25", f"d17_basic_decisions:{tag}", "wire_pins_to_net", f, W([("R1", "1")], "B"),
               script=S)
R.end()
