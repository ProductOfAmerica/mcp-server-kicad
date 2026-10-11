"""Rerun of verify-probe-decisions/v18_label_on_pin.py on M2 and K (DEC-18).

Fixtures copied from v18: R1:1 (63.5, 59.69) outward up lies on the interior of
  a one foreign wire R3:1 - R4:1     b plus a vertical foreign wire from R6:2 down to (63.5, 62.23)
     crossing the first exactly at R1:1's end (no junction)
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

R.begin("decisions", __file__, tag="dec_v18")
S = "verify-probe-decisions/v18_label_on_pin.py; rerun decisions/dec_v18.py"
RES = "Device:R"

for tag, cross in (("a", False), ("b", True)):
    p = D.v_new(f"v18{tag}")
    D.v_place(p, RES, "R1", 63.5, 63.5)
    D.v_place(p, RES, "R3", 50.8, 63.5)
    D.v_place(p, RES, "R4", 76.2, 63.5)
    D.wire(p, *D.pos(p, "R3", "1"), *D.pos(p, "R4", "1"))
    if cross:
        D.v_place(p, RES, "R6", 63.5, 40.64)
        D.wire(p, *D.pos(p, "R6", "2"), 63.5, 62.23)
    D.v_place(p, RES, "R5", 152.4, 101.6)
    D.v_stubbed(p, "R5", "1", "N")
    print(f"{tag}: R1:1 {D.pos(p, 'R1', '1')}")
    R.run_case("DEC-18", f"v18_label_on_pin:{tag}", "wire_pins_to_net", p, W([("R1", "1")], "N"),
               script=S)
R.end()
