"""Rerun of probe-geometry-misc/g2_diag_stub_end.py on M2 and K (GM-15: g2A, g2B, g2D; GM-02: g2C;
GM-04: g2E). Stub end or its label on or near a foreign 45-degree wire; fixture copied.
Raw controls (g2C raw, g2E raw) are not re-run (no design call).
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib as R  # noqa: E402  (first: redirects TMP/TEMP, no .pyc writes)

from rlib import H  # noqa: E402
import gmlib as G  # noqa: E402

SC = G.begin(__file__, "probe-geometry-misc/g2_diag_stub_end.py")
W = G.W(("R1", "1"))
V = "g2_diag_stub_end:"


def foo_net(p):
    H.place(p, "Device:R", "R3", 127, 101.6)
    H.wire(p, 127, 97.79, 127, 95.25)
    H.label(p, "FOO", 127, 95.25)


# A: lattice
p = H.fresh("g2A")
H.place(p, "Device:R", "R1", 101.6, 101.6)
H.place(p, "Device:R", "R2", 114.3, 96.52)  # R2:1 at (114.3, 92.71)
H.wire(p, 99.06, 97.79, 104.14, 92.71)
H.wire(p, 104.14, 92.71, 114.3, 92.71)
R.run_case("GM-15", V + "g2A", "wire_pins_to_net", p, W, script=SC,
           note="1x stub end on a 45-degree wire's interior (lattice point)")

# B: off-lattice exact
p = H.fresh("g2B")
H.place(p, "Device:R", "R1", 101.6, 101.6)
foo_net(p)
H.wire(p, 99.06, 98.33, 104.14, 93.25)
H.label(p, "FOO", 104.14, 93.25)
R.run_case("GM-15", V + "g2B", "wire_pins_to_net", p, dict(W, stub_length=2.0), script=SC,
           note="stub_length 2.0: stub end on a 45-degree wire at an off-lattice point")

# C / D: lattice stub end, diagonal shifted by 2 or 3 IU
for tag, dx, case in (("g2C", 0.0002, "GM-02"), ("g2D", 0.0003, "GM-15")):
    p = H.fresh(tag)
    H.place(p, "Device:R", "R1", 101.6, 101.6)
    foo_net(p)
    H.wire(p, 99.06 + dx, 97.79, 104.14 + dx, 92.71)
    H.label(p, "FOO", 104.14 + dx, 92.71)
    d2 = (dx * 1e4) ** 2 / 2
    R.run_case(case, V + tag, "wire_pins_to_net", p, W, script=SC,
               note=f"diagonal shifted {round(dx * 1e4)} IU: E1 at squared distance {d2:g} IU^2")

# E: label on the pin with an own wire ending at P and a diagonal sqrt(2) IU from P
p = H.fresh("g2E")
H.place(p, "Device:R", "R1", 101.6, 101.6)
H.place(p, "Device:R", "R5", 88.9, 101.6)  # R5:1 at (88.9, 97.79)
H.wire(p, 88.9, 97.79, 101.6, 97.79)       # own net: R1:1 - R5:1, ends exactly at P
foo_net(p)
for y in (95.25, 92.71, 90.17):           # block 1x, 2x and 3x stub ends with a foreign name
    H.label(p, "FOO2", 101.6, y)
H.place(p, "Device:R", "R6", 139.7, 101.6)
H.wire(p, 139.7, 97.79, 139.7, 95.25)
H.label(p, "FOO2", 139.7, 95.25)
# diagonal through (101.6002, 97.79): squared distance 2 IU^2 from P; lower-right end carries FOO
H.wire(p, 99.0602, 100.33, 104.1402, 95.25)
H.label(p, "FOO", 99.0602, 100.33)
R.run_case("GM-04", V + "g2E", "wire_pins_to_net", p, W, script=SC,
           note="label-on-pin fallback: own wire ends at P, foreign diagonal sqrt(2) IU from P")
R.end()
