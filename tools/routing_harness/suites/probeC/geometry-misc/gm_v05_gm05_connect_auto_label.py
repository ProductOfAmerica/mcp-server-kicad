"""Rerun of verify-probe-geometry-misc/v05_gm05_connect_auto_label.py on M2 and K (GM-05: a, b).
Raw controls ak1, ak2, ak3 are not re-run (no design call).
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

SC = G.begin(__file__, "verify-probe-geometry-misc/v05_gm05_connect_auto_label.py")
C = G.C("R1", "2", "R4", "1")


def fixture(name, d):
    p = H.fresh(name)
    H.place(p, "Device:R", "R1", 76.2, 76.2)
    H.place(p, "Device:R", "R4", 76.2, 93.98)  # R4:1 at (76.2, 90.17)
    H.place(p, "Device:R", "R3", 101.6, 76.2)
    H.wire(p, 101.6, 72.39, 101.6, 69.85)
    H.label(p, "FOO", 101.6, 69.85)
    y = round(80.01 + d, 4)
    H.wire(p, 71.12, y, 81.28, y)
    H.label(p, "FOO", 71.12, y)
    return p


R.run_case("GM-05", "v05_gm05_connect_auto_label:a", "connect_pins", fixture("a", 0.0001), C, script=SC,
           note="FOO wire 1 IU below pin 1, crossing the route")
R.run_case("GM-05", "v05_gm05_connect_auto_label:b", "connect_pins", fixture("b", 0.0002), C, script=SC,
           note="FOO wire 2 IU below pin 1 (boundary)")
R.end()
