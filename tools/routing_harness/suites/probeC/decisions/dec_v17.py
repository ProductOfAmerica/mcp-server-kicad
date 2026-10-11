"""Rerun of verify-probe-decisions/v17_junction_split.py on M2 and K (DEC-17).

Fixture copied from v17: R1:1 (50.8, 59.69) outward up; R2:2 (50.8, 41.91) above it; wire R1:1-R2:2
drawn R1first or R2first. Calls (fresh copy each): multi wire([R1:1, R2:2],'N'); single
wire([R1:1],'N'). (The original's junction listing is moot: neither design writes a junction.)
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import dhelp as D  # noqa: E402  (imports rlib first)
from dhelp import R, W  # noqa: E402

R.begin("decisions", __file__, tag="dec_v17")
S = "verify-probe-decisions/v17_junction_split.py; rerun decisions/dec_v17.py"
RES = "Device:R"

for order in ("R1first", "R2first"):
    p = D.v_new(f"v17{order}")
    D.v_place(p, RES, "R1", 50.8, 63.5)
    D.v_place(p, RES, "R2", 50.8, 38.1)
    a, b = (50.8, 59.69), (50.8, 41.91)
    D.wire(p, *(a + b if order == "R1first" else b + a))
    for tag, pl in (("multi", [("R1", "1"), ("R2", "2")]), ("single", [("R1", "1")])):
        R.run_case("DEC-17", f"v17_junction_split:{order}:{tag}", "wire_pins_to_net", p,
                   W(pl, "N"), script=S)
R.end()
