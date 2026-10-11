"""Rerun of verify-probe-geometry-misc/v09_gm09_pin_on_captured_wire.py on M2 and K (GM-09: a, b).
Raw controls (no junction; pre-split plus junction) are not re-run (no design call).
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

SC = G.begin(__file__, "verify-probe-geometry-misc/v09_gm09_pin_on_captured_wire.py")
W = G.W(("R1", "2"))


def fixture(name, a, b):
    p = H.fresh(name)
    H.place(p, "Device:R", "R1", 76.2, 76.2)
    H.wire(p, a, 80.01, b, 80.01)
    H.label(p, "FOO", 71.12, 80.0099)
    H.place(p, "Device:R", "R3", 101.6, 76.2)
    H.wire(p, 101.6, 72.39, 101.6, 69.85)
    H.label(p, "FOO", 101.6, 69.85)
    return p


for tag, a, b in (("a", 69.85, 82.55), ("b", 82.55, 69.85)):
    R.run_case("GM-09", f"v09_gm09_pin_on_captured_wire:{tag}", "wire_pins_to_net", fixture(tag, a, b), W,
               script=SC, note=f"'inert' wire through P, FOO by 1 IU capture (drawn {a}->{b})")
R.end()
