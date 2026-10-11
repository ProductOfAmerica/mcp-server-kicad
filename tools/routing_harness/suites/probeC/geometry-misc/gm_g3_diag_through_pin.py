"""Rerun of probe-geometry-misc/g3_diag_through_pin.py on M2 and K (GM-12: g3A, g3B, g3C).
A foreign 45-degree wire exactly through the target pin end; fixture copied.
Raw control g3Ak is not re-run (no design call).
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

SC = G.begin(__file__, "probe-geometry-misc/g3_diag_through_pin.py")
W = G.W(("R1", "1"))
V = "g3_diag_through_pin:"


def fixture(name, foreign=True, r4=False):
    p = H.fresh(name)
    H.place(p, "Device:R", "R1", 101.6, 101.6)
    H.wire(p, 99.06, 100.33, 104.14, 95.25)
    if foreign:
        H.place(p, "Device:R", "R2", 114.3, 99.06)  # R2:1 at (114.3, 95.25)
        H.wire(p, 104.14, 95.25, 114.3, 95.25)
    if r4:
        H.place(p, "Device:R", "R4", 127, 101.6)  # R4:1 at (127, 97.79)
    return p


R.run_case("GM-12", V + "g3A", "wire_pins_to_net", fixture("g3A"), W, script=SC,
           note="foreign 45-degree wire through the target pin end")
R.run_case("GM-12", V + "g3B", "wire_pins_to_net", fixture("g3B", foreign=False), W, script=SC,
           note="inert 45-degree wire through the target pin end")
R.run_case("GM-12", V + "g3C", "connect_pins", fixture("g3C", r4=True), G.C("R1", "1", "R4", "1"),
           script=SC, note="connect_pins from a pin end lying on a foreign 45-degree wire")
R.end()
