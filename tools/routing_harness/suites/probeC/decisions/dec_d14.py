"""Rerun of probe-decisions/d14_connect_pins_false_noop.py on M2 and K (DEC-06; also DEC-07, DEC-08).

Fixtures copied from d14 (R1:1 at (101.6, 97.79); R5:1 in line, on local label N):
  14a label N + NC flag stacked on the interior of wire R1:1-R2:1
  14b label N on R3:1's pin end lying on the interior of wire R1:1-R2:1
  14c R1:1's wire ends on a bus entry's bus-side end; the entry's wire side reaches N
  14d R1:1's wire ends on bus entry E1, whose other end is shared with bus entry E2 (no bus)
Call: connect_pins(R1:1, R5:1).
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import dhelp as D  # noqa: E402  (imports rlib first)
from dhelp import C, R  # noqa: E402

R.begin("decisions", __file__, tag="dec_d14")
S = "probe-decisions/d14_connect_pins_false_noop.py; rerun decisions/dec_d14.py"
RES = "Device:R"
ALSO = ["DEC-07", "DEC-08"]


def base(name):
    p = D.fresh(name)
    D.hplace(p, RES, "R1", 101.6, 101.6)    # R1:1 (101.6, 97.79)
    D.hplace(p, RES, "R5", 177.8, 101.6)    # R5:1 (177.8, 97.79), in line with R1:1
    D.d_stub_label(p, "R5", "1", "N")
    return p


def f14a():
    p = base("d14a")
    D.hplace(p, RES, "R2", 127, 101.6)
    D.wire(p, 101.6, 97.79, 127, 97.79)
    D.label(p, "N", 114.3, 97.79)
    D.no_connect(p, 114.3, 97.79)
    return p


def f14b():
    p = base("d14b")
    D.hplace(p, RES, "R2", 127, 101.6)
    D.hplace(p, RES, "R3", 114.3, 101.6)
    D.wire(p, 101.6, 97.79, 127, 97.79)
    D.label(p, "N", 114.3, 97.79)
    return p


def f14c():
    p = base("d14c")
    D.bus(p, 114.3, 88.9, 139.7, 88.9)
    D.bus_entry(p, 111.76, 91.44, 2.54, -2.54)
    D.wire(p, 101.6, 97.79, 101.6, 88.9)
    D.wire(p, 101.6, 88.9, 114.3, 88.9)
    D.hplace(p, RES, "R6", 106.68, 95.25)
    D.wire(p, 111.76, 91.44, 106.68, 91.44)
    D.label(p, "N", 106.68, 91.44)
    return p


def f14d():
    p = base("d14d")
    D.bus_entry(p, 101.6, 95.25, 2.54, -2.54)
    D.bus_entry(p, 106.68, 95.25, -2.54, -2.54)
    D.wire(p, 101.6, 97.79, 101.6, 95.25)
    D.hplace(p, RES, "R6", 106.68, 101.6)
    D.wire(p, 106.68, 95.25, 106.68, 97.79)
    D.label(p, "N", 106.68, 97.79)
    return p


for tag, build in (("14a", f14a), ("14b", f14b), ("14c", f14c), ("14d", f14d)):
    p = build()
    R.run_case("DEC-06", f"d14_connect_pins_false_noop:{tag}", "connect_pins", p,
               C("R1", "1", "R5", "1"), script=S, also=ALSO)
R.end()
