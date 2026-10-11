"""Rerun of verify-probe-decisions/v06_false_noop.py on M2 and K (DEC-06; also DEC-07, DEC-08).

Fixtures copied from v06 (R1:1 at (50.8, 46.99); R5:1 (152.4, 72.39) on the real local label N):
  6a wire R1:1-R2:1; label N + NC flag stacked at (63.5, 46.99)
  6b same wire; R3:1 on its interior with label N on R3:1's end
  6c bus; R1:1 wired to a bus entry's BUS-side end; entry wire side -> R6:1 with label N
  6d entries E1/E2 share a point, no bus; R1:1 -> E1; E2 -> R6:1 with label N
  6e-LR / 6e-RL wire with label N at its left end and R2:1 at its right end; R1:1 stub up to its
     interior with a junction there; drawn from / toward N's end
  6f wire R1:1-R2:1; labels N and M stacked; R7:1 on label M elsewhere
Calls (fresh copy each): wire([R1:1],'N'), wire([R1:1],'Z'), and for 6a-6d connect(R1:1, R5:1).
Not re-run: the raw-label truth (control) and the literal-M columns (an emulator, not a design;
v07_literal_M.py evaluates the same fixtures and is not duplicated).
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import dhelp as D  # noqa: E402  (imports rlib first)
from dhelp import C, R, W  # noqa: E402

R.begin("decisions", __file__, tag="dec_v06")
S = "verify-probe-decisions/v06_false_noop.py; rerun decisions/dec_v06.py"
RES = "Device:R"
ALSO = ["DEC-07", "DEC-08"]


def base(name):
    p = D.v_new(name)
    D.v_place(p, RES, "R1", 50.8, 50.8)
    D.v_place(p, RES, "R5", 152.4, 76.2)
    D.v_stubbed(p, "R5", "1", "N")
    return p


def f6a():
    p = base("v06a")
    D.v_place(p, RES, "R2", 76.2, 50.8)
    D.wire(p, 50.8, 46.99, 76.2, 46.99)
    D.label(p, "N", 63.5, 46.99)
    D.no_connect(p, 63.5, 46.99)
    return p


def f6b():
    p = base("v06b")
    D.v_place(p, RES, "R2", 76.2, 50.8)
    D.v_place(p, RES, "R3", 63.5, 50.8)
    D.wire(p, 50.8, 46.99, 76.2, 46.99)
    D.label(p, "N", 63.5, 46.99)
    return p


def f6c():
    p = base("v06c")
    D.bus(p, 63.5, 38.1, 88.9, 38.1)
    D.bus_entry(p, 60.96, 40.64, 2.54, -2.54)
    D.wire(p, 50.8, 46.99, 50.8, 38.1)
    D.wire(p, 50.8, 38.1, 63.5, 38.1)
    D.v_place(p, RES, "R6", 55.88, 44.45)
    D.wire(p, 60.96, 40.64, 55.88, 40.64)
    D.label(p, "N", 55.88, 40.64)
    return p


def f6d():
    p = base("v06d")
    D.bus_entry(p, 50.8, 44.45, 2.54, -2.54)
    D.bus_entry(p, 55.88, 44.45, -2.54, -2.54)
    D.wire(p, 50.8, 46.99, 50.8, 44.45)
    D.v_place(p, RES, "R6", 55.88, 50.8)
    D.wire(p, 55.88, 44.45, 55.88, 46.99)
    D.label(p, "N", 55.88, 46.99)
    return p


def f6e(order):
    def b():
        p = base(f"v06e{order}")
        D.v_place(p, RES, "R2", 76.2, 48.26)
        a, z = (38.1, 44.45), (76.2, 44.45)
        if order == "RL":
            a, z = z, a
        D.wire(p, *a, *z)
        D.label(p, "N", 38.1, 44.45)
        D.wire(p, 50.8, 46.99, 50.8, 44.45)
        D.junction(p, 50.8, 44.45)
        return p
    return b


def f6f():
    p = base("v06f")
    D.v_place(p, RES, "R2", 76.2, 50.8)
    D.v_place(p, RES, "R7", 177.8, 76.2)
    D.v_stubbed(p, "R7", "1", "M")
    D.wire(p, 50.8, 46.99, 76.2, 46.99)
    D.label(p, "N", 63.5, 46.99)
    D.label(p, "M", 63.5, 46.99)
    return p


CASES = [("6a", f6a), ("6b", f6b), ("6c", f6c), ("6d", f6d), ("6e-LR", f6e("LR")),
         ("6e-RL", f6e("RL")), ("6f", f6f)]
for tag, build in CASES:
    p = build()
    print(f"{tag}: R1:1 at {D.pos(p, 'R1', '1')}")
    for N in ("N", "Z"):
        R.run_case("DEC-06", f"v06_false_noop:{tag}:wire{N}", "wire_pins_to_net", p,
                   W([("R1", "1")], N), script=S, also=ALSO)
    if tag in ("6a", "6b", "6c", "6d"):
        R.run_case("DEC-06", f"v06_false_noop:{tag}:connect", "connect_pins", p,
                   C("R1", "1", "R5", "1"), script=S, also=ALSO)
R.end()
