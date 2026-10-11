"""Rerun of verify-probe-geometry-misc/v06_gm06_name_model_capture.py on M2 and K (GM-06: a, b, c, d).
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

SC = G.begin(__file__, "verify-probe-geometry-misc/v06_gm06_name_model_capture.py")
W = G.W(("R1", "2"))
V = "v06_gm06_name_model_capture:"
WP = "wire_pins_to_net"


def base(name, n_holder=True):
    p = H.fresh(name)
    H.place(p, "Device:R", "R1", 76.2, 76.2)
    H.place(p, "Device:R", "R3", 101.6, 76.2)
    H.wire(p, 101.6, 72.39, 101.6, 69.85)
    H.label(p, "FOO", 101.6, 69.85)
    if n_holder:
        H.place(p, "Device:R", "R6", 114.3, 76.2)
        H.wire(p, 114.3, 72.39, 114.3, 69.85)
        H.label(p, "N", 114.3, 69.85)
    return p


def own_h(p, dy):
    H.place(p, "Device:R", "R5", 88.9, 76.2)  # R5:2 at (88.9, 80.01)
    H.wire(p, 76.2, 80.01, 88.9, 80.01)
    H.label(p, "FOO", 82.55, round(80.01 - dy, 4))


p = base("a")
own_h(p, 0.0001)
R.run_case("GM-06", V + "a", WP, p, W, script=SC, note="own horizontal wire, FOO 1 IU above it")

p = base("b")
H.wire(p, 76.2, 80.01, 74.93, 82.55)
H.label(p, "FOO", 75.5652, 81.28)
dx, dy = G.iu(75.5652) - G.iu(76.2), G.iu(81.28) - G.iu(80.01)
print(f"b: label offset ({dx}, {dy}) IU; exact d^2 = (2*dx+dy)^2/5 = {(2 * dx + dy) ** 2 / 5}")
R.run_case("GM-06", V + "b", WP, p, W, script=SC, note="own slope-2 wire, FOO at d^2 = 3.2 IU^2")

p = base("c", n_holder=False)
own_h(p, 0.0001)
H.place(p, "Device:R", "R4", 76.2, 93.98)  # R4:1 at (76.2, 90.17)
H.wire(p, 76.2, 90.17, 71.12, 90.17)
H.label(p, "BAR", 71.12, 90.17)
R.run_case("GM-06", V + "c", "connect_pins", p, G.C("R1", "2", "R4", "1"), script=SC,
           note="connect_pins R1:2 (FOO by capture) -> R4:1 (BAR)")

p = base("d")
own_h(p, 0.0002)
R.run_case("GM-06", V + "d", WP, p, W, script=SC, note="own horizontal wire, FOO 2 IU above (not attached)")
R.end()
