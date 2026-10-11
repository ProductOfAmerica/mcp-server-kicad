"""Rerun of probe-geometry-misc/c1_connect_label_near.py on M2 and K (GM-05: c1a, c1b; also GM-10 and
GM-11). connect_pins with an off-grid foreign wire 1 or 2 IU beside pin 1; fixture copied.
Raw control c1ak (route only) is not re-run (no design call).
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

SC = G.begin(__file__, "probe-geometry-misc/c1_connect_label_near.py")


def fixture(name, d):
    p = H.fresh(name)
    H.place(p, "Device:R", "R1", 101.6, 101.6)
    H.place(p, "Device:R", "R4", 127, 101.6)
    x = round(101.6 + d, 4)
    H.wire(p, x, 92.71, x, 102.87)
    H.label(p, "FOO", x, 92.71)
    H.place(p, "Device:R", "R3", 152.4, 127)
    H.wire(p, 152.4, 123.19, 152.4, 120.65)
    H.label(p, "FOO", 152.4, 120.65)
    return p


for tag, d in (("c1a", 0.0001), ("c1b", 0.0002)):
    R.run_case("GM-05", f"c1_connect_label_near:{tag}", "connect_pins", fixture(tag, d),
               G.C("R1", "1", "R4", "1"), script=SC, also=["GM-10", "GM-11"],
               note=f"connect_pins R1:1 -> R4:1, FOO wire {round(d * 1e4)} IU right of pin 1")
R.end()
