"""Rerun of probe-decisions/d09_listed_pins_connected.py on M2 and K (DEC-24 a/b, DEC-17 c).

Fixtures copied from d09:
  a [R1:1, R2:1] joined by an existing plain wire -> N
  b [R1:2, R2:1] coincident pin ends (no wire) -> N
  c [R1:1, R2:2] joined by a wire along R1:1's outward direction, drawn R1first or R2first -> N
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import dhelp as D  # noqa: E402  (imports rlib first)
from dhelp import R, W  # noqa: E402

R.begin("decisions", __file__, tag="dec_d09")
S = "probe-decisions/d09_listed_pins_connected.py; rerun decisions/dec_d09.py"
RES = "Device:R"

fa = D.fresh("d09a")
D.hplace(fa, RES, "R1", 101.6, 101.6)
D.hplace(fa, RES, "R2", 127, 101.6)
D.wire(fa, *D.pos(fa, "R1", "1"), *D.pos(fa, "R2", "1"))

fb = D.fresh("d09b")
D.hplace(fb, RES, "R1", 101.6, 101.6)          # R1:2 (101.6, 105.41)
D.hplace(fb, RES, "R2", 101.6, 109.22)         # R2:1 (101.6, 105.41): coincident

fc = {}
for order in ("R1first", "R2first"):
    f = D.fresh(f"d09c_{order}")
    D.hplace(f, RES, "R1", 101.6, 101.6)       # R1:1 (101.6, 97.79), outward up
    D.hplace(f, RES, "R2", 101.6, 81.28)       # R2:2 (101.6, 85.09)
    a, b = D.pos(f, "R1", "1"), D.pos(f, "R2", "2")
    D.wire(f, *(a + b if order == "R1first" else b + a))
    fc[order] = f

R.run_case("DEC-24", "d09_listed_pins_connected:a", "wire_pins_to_net", fa,
           W([("R1", "1"), ("R2", "1")], "N"), script=S)
R.run_case("DEC-24", "d09_listed_pins_connected:b", "wire_pins_to_net", fb,
           W([("R1", "2"), ("R2", "1")], "N"), script=S)
for order in ("R1first", "R2first"):
    R.run_case("DEC-17", f"d09_listed_pins_connected:c-{order}", "wire_pins_to_net", fc[order],
               W([("R1", "1"), ("R2", "2")], "N"), script=S, also=["DEC-24"])
R.end()
