"""Rerun of verify-probe-geometry-misc/v04_gm04_label_on_pin_two_lines.py on M2 and K (GM-04: a, b, c, d;
c is also cited by GM-11 as 'the v04 control c', a design call). Raw controls (a raw, c raw) are not
re-run (no design call).
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

SC = G.begin(__file__, "verify-probe-geometry-misc/v04_gm04_label_on_pin_two_lines.py")
W = G.W(("R1", "2"))
V = "v04_gm04_label_on_pin_two_lines:"
WP = "wire_pins_to_net"


def base(name, own=True):
    p = H.fresh(name)
    H.place(p, "Device:R", "R1", 76.2, 76.2)
    if own:
        H.place(p, "Device:R", "R5", 63.5, 76.2)  # R5:2 at (63.5, 80.01)
        H.wire(p, 63.5, 80.01, 76.2, 80.01)
    H.place(p, "Device:R", "R3", 101.6, 76.2)
    H.wire(p, 101.6, 72.39, 101.6, 69.85)
    H.label(p, "FOO", 101.6, 69.85)
    H.place(p, "Device:R", "R6", 114.3, 76.2)
    H.wire(p, 114.3, 72.39, 114.3, 69.85)
    H.label(p, "FOO2", 114.3, 69.85)
    for y in (82.55, 85.09, 87.63):
        H.label(p, "FOO2", 76.2, y)
    return p


def vert_foo(p, dx):
    x = round(76.2 + dx, 4)
    H.wire(p, x, 78.74, x, 81.28)
    H.label(p, "FOO", x, 78.74)


p = base("a")
vert_foo(p, 0.0001)
R.run_case("GM-04", V + "a", WP, p, W, script=SC, note="own wire ends at P; vertical FOO wire 1 IU right of P")

p = base("b")
# 45-degree line x + y = 156.2102 (P: 76.2 + 80.01 = 156.21): perpendicular distance 2/sqrt2 IU
H.wire(p, 73.6602, 82.55, 78.7402, 77.47)
H.label(p, "FOO", 78.7402, 77.47)
R.run_case("GM-04", V + "b", WP, p, W, script=SC, note="own wire ends at P; 45-degree FOO wire at d^2 = 2 IU^2 from P")

p = base("c", own=False)
vert_foo(p, 0.0001)
R.run_case("GM-04", V + "c", WP, p, W, script=SC, also=["GM-11"],
           note="NO own wire (bare pin); vertical FOO wire 1 IU right of P")

p = base("d")
vert_foo(p, 0.0002)
R.run_case("GM-04", V + "d", WP, p, W, script=SC,
           note="own wire ends at P; vertical FOO wire 2 IU right of P (boundary)")
R.end()
