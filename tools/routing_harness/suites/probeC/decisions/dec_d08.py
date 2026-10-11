"""Rerun of probe-decisions/d08_same_pin_twice.py on M2 and K (DEC-24 a/b/d, DEC-12 c).

Fixtures copied from d08: p = R1 + Device:D D1; q = Device:R_Network03_Split RN1 unit A plus unit B
added by add_unit (pin 1 is a unit-0 pad drawn by both).
  a [R1:1, R1:1] -> N   b [D1:'1', D1:'K'] -> N   c [RN1:1, RN1:1] -> N   d connect(R1:1, R1:1)
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import dhelp as D  # noqa: E402  (imports rlib first)
from dhelp import C, R, W  # noqa: E402

R.begin("decisions", __file__, tag="dec_d08")
S = "probe-decisions/d08_same_pin_twice.py; rerun decisions/dec_d08.py"

p = D.fresh("d08")
D.hplace(p, "Device:R", "R1", 101.6, 101.6)
D.hplace(p, "Device:D", "D1", 152.4, 101.6)
q = D.fresh("d08c")
D.hplace(q, "Device:R_Network03_Split", "RN1", 101.6, 101.6)
D.d_add_unit(q, "RN1", 2, 127, 101.6)

R.run_case("DEC-24", "d08_same_pin_twice:a", "wire_pins_to_net", p,
           W([("R1", "1"), ("R1", "1")], "N"), script=S)
R.run_case("DEC-24", "d08_same_pin_twice:b", "wire_pins_to_net", p,
           W([("D1", "1"), ("D1", "K")], "N"), script=S)
R.run_case("DEC-12", "d08_same_pin_twice:c", "wire_pins_to_net", q,
           W([("RN1", "1"), ("RN1", "1")], "N"), script=S, also=["DEC-24"])
R.run_case("DEC-24", "d08_same_pin_twice:d", "connect_pins", p, C("R1", "1", "R1", "1"), script=S)
R.end()
