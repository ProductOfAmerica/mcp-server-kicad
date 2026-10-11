"""Rerun of verify-probe-geometry-misc/v07_gm07_directive.py on M2 and K (GM-07: a, b, c, d, e).
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

SC = G.begin(__file__, "verify-probe-geometry-misc/v07_gm07_directive.py")
W = G.W(("R1", "2"))
V = "v07_gm07_directive:"
WP = "wire_pins_to_net"


def base(name, n_holder=False):
    p = H.fresh(name)
    H.place(p, "Device:R", "R1", 76.2, 76.2)
    if n_holder:
        H.place(p, "Device:R", "R6", 114.3, 76.2)
        H.wire(p, 114.3, 72.39, 114.3, 69.85)
        H.label(p, "N", 114.3, 69.85)
    return p


p = base("a")
H.directive(p, "HV", 76.2, 81.28)
R.run_case("GM-07", V + "a", WP, p, W, script=SC, note="lone HV flag on the stub interior")

p = base("b")
H.directive(p, "HV", 76.2, 82.55)
R.run_case("GM-07", V + "b", WP, p, W, script=SC, note="lone HV flag at E1")

p = base("c")
H.wire(p, 76.2, 82.55, 81.28, 82.55)
H.directive(p, "HV", 81.28, 82.55)
R.run_case("GM-07", V + "c", WP, p, W, script=SC, note="dangling wire ending at E1 with an HV flag on it")

p = base("d", n_holder=True)
H.wire(p, 76.2, 82.55, 81.28, 82.55)
H.directive(p, "HV", 81.28, 82.55)
R.run_case("GM-07", V + "d", WP, p, W, script=SC, note="as c, N already exists on R6:1")

p = base("e", n_holder=True)
H.wire(p, 114.3, 69.85, 119.38, 69.85)
H.directive(p, "HV", 119.38, 69.85)
R.run_case("GM-07", V + "e", WP, p, W, script=SC,
           note="control: HV flag sits on N's own net (class comes with the requested join)")
R.end()
