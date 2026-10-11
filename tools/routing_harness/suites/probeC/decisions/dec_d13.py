"""Rerun of probe-decisions/d13_unnamed_per_P_named_per_kicad.py on M2 and K (DEC-16 a/b; DEC-08 c).

Fixture copied from d13: bus (114.3, 88.9)-(139.7, 88.9); R1:1 wired sideways then up to the bus's
left end; R6:1 wired to the bus's right end with label M. At both bus ends: a NC flags,
b junctions, c nothing (control geometry). R5:1 on N elsewhere. Call: wire([R1:1],'N').
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import dhelp as D  # noqa: E402  (imports rlib first)
from dhelp import R, W  # noqa: E402

R.begin("decisions", __file__, tag="dec_d13")
S = "probe-decisions/d13_unnamed_per_P_named_per_kicad.py; rerun decisions/dec_d13.py"
RES = "Device:R"


def fixture(name, mark):
    p = D.fresh(name)
    D.hplace(p, RES, "R1", 101.6, 101.6)                  # R1:1 (101.6, 97.79), outward up
    D.wire(p, 101.6, 97.79, 114.3, 97.79)
    D.wire(p, 114.3, 97.79, 114.3, 88.9)                  # ends on the bus's left end
    D.bus(p, 114.3, 88.9, 139.7, 88.9)
    D.hplace(p, RES, "R6", 152.4, 92.71)                  # R6:1 (152.4, 88.9)
    D.wire(p, 139.7, 88.9, 152.4, 88.9)                   # bus right end -> R6:1
    D.label(p, "M", 152.4, 88.9)
    if mark:
        for x in (114.3, 139.7):
            mark(p, x, 88.9)
    D.hplace(p, RES, "R5", 177.8, 127)
    D.d_stub_label(p, "R5", "1", "N")
    return p


for tag, mark, case, also in (("a", D.no_connect, "DEC-16", []), ("b", D.junction, "DEC-16", []),
                              ("c", None, "DEC-08", ["DEC-16"])):
    p = fixture(f"d13{tag}", mark)
    R.run_case(case, f"d13_unnamed_per_P_named_per_kicad:{tag}", "wire_pins_to_net", p,
               W([("R1", "1")], "N"), script=S, also=also)
R.end()
