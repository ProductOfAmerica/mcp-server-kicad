"""Rerun of probe-geometry-misc/n1_directive_near_stub.py on M2 and K (GM-07: n1a, n1b, n1c).
A lone directive label (netclass_flag HV) on or next to the new stub; fixture copied.
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

SC = G.begin(__file__, "probe-geometry-misc/n1_directive_near_stub.py")
W = G.W(("R1", "1"))

for tag, x, y, what in (("n1a", 101.6, 96.52, "exactly on the stub interior"),
                        ("n1b", 101.6001, 96.52, "1 IU beside the stub interior"),
                        ("n1c", 101.6, 95.25, "exactly at the stub end E1")):
    p = H.fresh(tag)
    H.place(p, "Device:R", "R1", 101.6, 101.6)
    H.directive(p, "HV", x, y)
    R.run_case("GM-07", f"n1_directive_near_stub:{tag}", "wire_pins_to_net", p, W, script=SC,
               note=f"lone netclass_flag HV {what}")
R.end()
