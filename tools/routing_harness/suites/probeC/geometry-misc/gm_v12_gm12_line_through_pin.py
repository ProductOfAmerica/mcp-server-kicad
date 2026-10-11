"""Rerun of verify-probe-geometry-misc/v12_gm12_line_through_pin.py on M2 and K (GM-12: a, b, c, d).
Raw controls (stub + label N; label N on P; route only) are not re-run (no design call).
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

SC = G.begin(__file__, "verify-probe-geometry-misc/v12_gm12_line_through_pin.py")
W = G.W(("R1", "2"))
V = "v12_gm12_line_through_pin:"


def fixture(name, kind, r4=False):
    p = H.fresh(name)
    H.place(p, "Device:R", "R1", 76.2, 76.2)
    if kind == "diag":
        H.wire(p, 73.66, 82.55, 78.74, 77.47)
        H.place(p, "Device:R", "R2", 88.9, 81.28)  # R2:1 at (88.9, 77.47)
        H.wire(p, 78.74, 77.47, 88.9, 77.47)
    elif kind == "inert":
        H.wire(p, 69.85, 80.01, 82.55, 80.01)
    elif kind == "bus":
        H.bus(p, 69.85, 80.01, 82.55, 80.01)
        H.label(p, "D[0..3]", 69.85, 80.01)
    if r4:
        H.place(p, "Device:R", "R4", 76.2, 93.98)
    return p


for tag, kind in (("a", "diag"), ("b", "inert"), ("c", "bus")):
    R.run_case("GM-12", V + tag, "wire_pins_to_net", fixture(tag, kind), W, script=SC,
               note=f"{kind} line through P")

R.run_case("GM-12", V + "d", "connect_pins", fixture("d", "diag", r4=True), G.C("R1", "2", "R4", "1"),
           script=SC, note="connect_pins R1:2 -> R4:1 with a foreign diagonal through R1:2")
R.end()
