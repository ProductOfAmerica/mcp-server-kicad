"""Rerun of verify-probe-decisions/v19_autoname.py on M2 and K (DEC-19).

Fixture copied from v19: R1:1 (63.5, 59.69) and R2:1 (88.9, 59.69) wired together (KiCad:
Net-(R1-Pad1)); R7:1 (139.7, 59.69) free.
  a wire([R7:1],'Net-(R1-Pad1)')   b wire([R1:1],'Net-(R1-Pad1)')
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import dhelp as D  # noqa: E402  (imports rlib first)
from dhelp import R, W  # noqa: E402

R.begin("decisions", __file__, tag="dec_v19")
S = "verify-probe-decisions/v19_autoname.py; rerun decisions/dec_v19.py"
RES = "Device:R"

p = D.v_new("v19")
D.v_place(p, RES, "R1", 63.5, 63.5)
D.v_place(p, RES, "R2", 88.9, 63.5)
D.v_place(p, RES, "R7", 139.7, 63.5)
D.wire(p, *D.pos(p, "R1", "1"), *D.pos(p, "R2", "1"))
for tag, tgt in (("a", ("R7", "1")), ("b", ("R1", "1"))):
    R.run_case("DEC-19", f"v19_autoname:{tag}", "wire_pins_to_net", p, W([tgt], "Net-(R1-Pad1)"),
               script=S)
R.end()
