"""Rerun of probe-decisions/d06_false_already_on_N.py on M2 and K (DEC-06; also DEC-07, DEC-08).

Fixtures copied from d06 (R1:1 at (101.6, 97.79); R5:1 on a real local label N elsewhere):
  6a label N + NC flag stacked on the interior of wire R1:1-R2:1
  6b label N on R3:1's pin end, which lies on the interior of wire R1:1-R2:1
  6c R1:1's wire ends on the BUS-side end of a bus entry whose wire side reaches N
  6d R1:1's wire ends on bus entry E1 sharing its other end with bus entry E2 (no bus), E2 -> N
  6e-LR / 6e-RL R1:1's stub ends on the interior of a wire carrying N, junction there
  6f labels N and M stacked on the interior of wire R1:1-R2:1
Calls (fresh copy each): wire([R1:1],'N') and wire([R1:1],'Z'). The raw-label truths are
controls, not re-run.
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import dhelp as D  # noqa: E402  (imports rlib first)
from dhelp import R, W  # noqa: E402

R.begin("decisions", __file__, tag="dec_d06")
S = "probe-decisions/d06_false_already_on_N.py; rerun decisions/dec_d06.py"
RES = "Device:R"
ALSO = ["DEC-07", "DEC-08"]


def base(name):
    p = D.fresh(name)
    D.hplace(p, RES, "R1", 101.6, 101.6)    # R1:1 (101.6, 97.79)
    D.hplace(p, RES, "R5", 177.8, 101.6)
    D.d_stub_label(p, "R5", "1", "N")      # the existing net N
    return p


def f6a():
    p = base("d06a")
    D.hplace(p, RES, "R2", 127, 101.6)
    D.wire(p, 101.6, 97.79, 127, 97.79)
    D.label(p, "N", 114.3, 97.79)
    D.no_connect(p, 114.3, 97.79)
    return p


def f6b():
    p = base("d06b")
    D.hplace(p, RES, "R2", 127, 101.6)
    D.hplace(p, RES, "R3", 114.3, 101.6)    # R3:1 (114.3, 97.79) on the wire interior
    D.wire(p, 101.6, 97.79, 127, 97.79)
    D.label(p, "N", 114.3, 97.79)
    return p


def f6c():
    p = base("d06c")
    D.bus(p, 114.3, 88.9, 139.7, 88.9)
    D.bus_entry(p, 111.76, 91.44, 2.54, -2.54)       # ends (111.76, 91.44) and (114.3, 88.9)
    D.wire(p, 101.6, 97.79, 101.6, 88.9)
    D.wire(p, 101.6, 88.9, 114.3, 88.9)              # ends on the bus-side end
    D.hplace(p, RES, "R6", 106.68, 95.25)            # R6:1 (106.68, 91.44)
    D.wire(p, 111.76, 91.44, 106.68, 91.44)          # entry wire side -> R6:1
    D.label(p, "N", 106.68, 91.44)
    return p


def f6d():
    p = base("d06d")
    D.bus_entry(p, 101.6, 95.25, 2.54, -2.54)        # E1: (101.6, 95.25) - (104.14, 92.71)
    D.bus_entry(p, 106.68, 95.25, -2.54, -2.54)      # E2: (106.68, 95.25) - (104.14, 92.71)
    D.wire(p, 101.6, 97.79, 101.6, 95.25)            # R1:1 -> E1
    D.hplace(p, RES, "R6", 106.68, 101.6)            # R6:1 (106.68, 97.79)
    D.wire(p, 106.68, 95.25, 106.68, 97.79)          # E2 -> R6:1
    D.label(p, "N", 106.68, 97.79)
    return p


def f6e(order):
    def build():
        p = base(f"d06e_{order}")
        D.hplace(p, RES, "R2", 114.3, 99.06)         # R2:1 (114.3, 95.25), the wire's right end
        a, b = (88.9, 95.25), (114.3, 95.25)
        if order == "RL":
            a, b = b, a
        D.wire(p, *a, *b)
        D.label(p, "N", 88.9, 95.25)
        D.wire(p, 101.6, 97.79, 101.6, 95.25)        # R1:1 stub to the wire interior
        D.junction(p, 101.6, 95.25)
        return p
    return build


def f6f():
    p = base("d06f")
    D.hplace(p, RES, "R2", 127, 101.6)
    D.hplace(p, RES, "R7", 152.4, 101.6)
    D.d_stub_label(p, "R7", "1", "M")
    D.wire(p, 101.6, 97.79, 127, 97.79)
    D.label(p, "N", 114.3, 97.79)
    D.label(p, "M", 114.3, 97.79)
    return p


CASES = [("6a", f6a), ("6b", f6b), ("6c", f6c), ("6d", f6d), ("6e-LR", f6e("LR")),
         ("6e-RL", f6e("RL")), ("6f", f6f)]
for tag, build in CASES:
    p = build()
    for N in ("N", "Z"):
        R.run_case("DEC-06", f"d06_false_already_on_N:{tag}:wire{N}", "wire_pins_to_net", p,
                   W([("R1", "1")], N), script=S, also=ALSO)
R.end()
