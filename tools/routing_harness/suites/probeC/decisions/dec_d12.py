"""Rerun of probe-decisions/d12_label_on_pin_fallback.py on M2 and K (DEC-18).

Fixtures copied from d12: R1:1's end (101.6, 97.79) lies on the interior of
  a one foreign wire R3:1-R4:1     b plus a second foreign wire R6:2-R7:1 crossing exactly there
R5:1 on N. Call: wire([R1:1],'N'). The raw-label truth is a control, not re-run.
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import dhelp as D  # noqa: E402  (imports rlib first)
from dhelp import R, W  # noqa: E402

R.begin("decisions", __file__, tag="dec_d12")
S = "probe-decisions/d12_label_on_pin_fallback.py; rerun decisions/dec_d12.py"
RES = "Device:R"


def fixture(name, crossing):
    p = D.fresh(name)
    D.hplace(p, RES, "R1", 101.6, 101.6)        # R1:1 (101.6, 97.79)
    D.hplace(p, RES, "R3", 88.9, 101.6)         # R3:1 (88.9, 97.79)
    D.hplace(p, RES, "R4", 114.3, 101.6)        # R4:1 (114.3, 97.79)
    D.wire(p, *D.pos(p, "R3", "1"), *D.pos(p, "R4", "1"))       # passes through R1:1's end
    if crossing:
        D.hplace(p, RES, "R6", 101.6, 81.28)    # R6:2 (101.6, 85.09)
        D.hplace(p, RES, "R7", 101.6, 114.3)    # R7:1 (101.6, 110.49)
        D.wire(p, *D.pos(p, "R6", "2"), *D.pos(p, "R7", "1"))   # vertical, also through R1:1's end
    D.hplace(p, RES, "R5", 177.8, 101.6)
    D.d_stub_label(p, "R5", "1", "N")
    return p


for tag, crossing in (("a", False), ("b", True)):
    p = fixture(f"d12{tag}", crossing)
    R.run_case("DEC-18", f"d12_label_on_pin_fallback:{tag}", "wire_pins_to_net", p,
               W([("R1", "1")], "N"), script=S)
R.end()
