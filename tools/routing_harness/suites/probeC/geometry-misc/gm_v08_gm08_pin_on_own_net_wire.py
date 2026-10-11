"""Rerun of verify-probe-geometry-misc/v08_gm08_pin_on_own_net_wire.py on M2 and K (GM-08: a, b; also
GM-12, whose verifier defers the N-carrying wire to v08). Raw controls (no junction, pre-split plus
junction, label on P only) are not re-run (no design call).
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

SC = G.begin(__file__, "verify-probe-geometry-misc/v08_gm08_pin_on_own_net_wire.py")
W = G.W(("R1", "2"))


def fixture(name, a, b):
    p = H.fresh(name)
    H.place(p, "Device:R", "R1", 76.2, 76.2)
    H.place(p, "Device:R", "R0", 63.5, 76.2)  # R0:2 at (63.5, 80.01)
    H.place(p, "Device:R", "R2", 88.9, 76.2)  # R2:2 at (88.9, 80.01)
    H.wire(p, a, 80.01, b, 80.01)
    H.label(p, "N", 63.5, 80.01)
    return p


for tag, a, b in (("a", 63.5, 88.9), ("b", 88.9, 63.5)):
    R.run_case("GM-08", f"v08_gm08_pin_on_own_net_wire:{tag}", "wire_pins_to_net", fixture(tag, a, b), W,
               script=SC, also=["GM-12"],
               note=f"R1:2 on the interior of N's wire (drawn {a}->{b}); wire R1:2 to N")
R.end()
