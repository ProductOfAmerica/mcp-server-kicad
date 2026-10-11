"""Rerun of verify-probe-geometry-misc/v11_gm11_m_tolerance.py on M2 and K (GM-11: a, a2, b, b2, c, e,
e2). Raw control 'c route only' is not re-run (no design call).
"""
import math
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib as R  # noqa: E402  (first: redirects TMP/TEMP, no .pyc writes)

from rlib import H  # noqa: E402
import gmlib as G  # noqa: E402

SC = G.begin(__file__, "verify-probe-geometry-misc/v11_gm11_m_tolerance.py")
W = G.W(("R1", "2"))
V = "v11_gm11_m_tolerance:"
WP = "wire_pins_to_net"
GR = 1.27


def base(name):
    p = H.fresh(name)
    H.place(p, "Device:R", "R1", 76.2, 76.2)
    H.place(p, "Device:R", "R3", 101.6, 76.2)
    H.wire(p, 101.6, 72.39, 101.6, 69.85)
    H.label(p, "FOO", 101.6, 69.85)
    return p


for tag, off in (("a", 0.05), ("a2", 0.0501)):
    p = base(tag)
    H.place(p, "Device:R", "R2", 80.01, 80.01, rot=90)
    G.set_at(p, "R2", round(80.01 + off, 4), 80.01)
    print(tag, "R2 pins:", G.kpin(p, "R2", "1"), G.kpin(p, "R2", "2"))
    R.run_case("GM-11", V + tag, WP, p, W, script=SC, note=f"foreign pin end {off} mm right of P")

for tag, y in (("b", 80.0099), ("b2", 80.0101)):
    p = base(tag)
    H.wire(p, 69.85, y, 82.55, y)
    H.label(p, "FOO", 69.85, y)
    R.run_case("GM-11", V + tag, WP, p, W, script=SC, note=f"FOO wire at y={y} (1 IU from P)")

p = base("c")
H.place(p, "Device:R", "R4", 76.2, 93.98)
H.wire(p, 71.12, 80.0102, 81.28, 80.0102)
H.label(p, "FOO", 71.12, 80.0102)
R.run_case("GM-11", V + "c", "connect_pins", p, G.C("R1", "2", "R4", "1"), script=SC,
           note="connect_pins, FOO wire 2 IU below pin 1")

# Lattice-only any-angle wires. P = (60, 63) lattice units.
for tag, A, step in (("e", (33, 62), (52, 2)), ("e2", (32, 32), (37, 41))):
    p = base(tag)
    ax, ay = A
    bx, by = ax + step[0], ay + step[1]
    H.wire(p, round(ax * GR, 4), round(ay * GR, 4), round(bx * GR, 4), round(by * GR, 4))
    H.label(p, "FOO", round(ax * GR, 4), round(ay * GR, 4))
    dxs, dys = step
    cross = abs((60 - ax) * dys - (63 - ay) * dxs)
    d_mm = cross * GR / math.hypot(dxs, dys)
    print(f"{tag}: wire ({ax * GR:g},{ay * GR:g})-({bx * GR:g},{by * GR:g}) mm, all lattice points; "
          f"distance from P = {d_mm:.4f} mm = {d_mm * 1e4:.0f} IU")
    R.run_case("GM-11", V + tag, WP, p, W, script=SC,
               note=f"lattice-only any-angle FOO wire {d_mm:.4f} mm from P")
R.end()
