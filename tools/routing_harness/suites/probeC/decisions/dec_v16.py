"""Rerun of verify-probe-decisions/v16_bus_bridge.py on M2 and K (DEC-16 a/b; DEC-08 c).

Fixture copied from v16: R1:1 (50.8, 59.69) wired right then up to the bus's left end (63.5, 50.8);
bus (63.5, 50.8)-(88.9, 50.8); wire on to R6:1 with label M on R6:1's end; R5:1 on N elsewhere.
At both bus ends: a NC flags, b junctions, c nothing (control). Call: wire([R1:1],'N').
The literal-M decision (v07 emulator) is not re-run.
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import dhelp as D  # noqa: E402  (imports rlib first)
from dhelp import R, W  # noqa: E402

R.begin("decisions", __file__, tag="dec_v16")
S = "verify-probe-decisions/v16_bus_bridge.py; rerun decisions/dec_v16.py"
RES = "Device:R"


def fixture(name, mark):
    p = D.v_new(name)
    D.v_place(p, RES, "R1", 50.8, 63.5)
    D.wire(p, 50.8, 59.69, 63.5, 59.69)
    D.wire(p, 63.5, 59.69, 63.5, 50.8)
    D.bus(p, 63.5, 50.8, 88.9, 50.8)
    D.v_place(p, RES, "R6", 101.6, 54.61)
    D.wire(p, 88.9, 50.8, 101.6, 50.8)
    D.label(p, "M", 101.6, 50.8)
    if mark:
        for x in (63.5, 88.9):
            mark(p, x, 50.8)
    D.v_place(p, RES, "R5", 152.4, 101.6)
    D.v_stubbed(p, "R5", "1", "N")
    return p


for tag, mark, case, also in (("a", D.no_connect, "DEC-16", []), ("b", D.junction, "DEC-16", []),
                              ("c", None, "DEC-08", ["DEC-16"])):
    p = fixture(f"v16{tag}", mark)
    R.run_case(case, f"v16_bus_bridge:{tag}", "wire_pins_to_net", p, W([("R1", "1")], "N"),
               script=S, also=also)
R.end()
