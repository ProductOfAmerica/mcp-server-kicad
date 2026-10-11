"""Rerun of probe-geometry-misc/i2_pin_on_own_net_wire.py on M2 and K (GM-08: i2a, i2b; also GM-12,
which cites the M rows). The target pin end on the interior of a wire already carrying N; fixture
copied. Raw control 'i2a raw' is not re-run (no design call).
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

SC = G.begin(__file__, "probe-geometry-misc/i2_pin_on_own_net_wire.py")
W = G.W(("R1", "1"))

for tag, a, b in (("i2a", 88.9, 114.3), ("i2b", 114.3, 88.9)):
    p = H.fresh(tag)
    H.place(p, "Device:R", "R1", 101.6, 101.6)
    H.place(p, "Device:R", "R2", 114.3, 101.6)
    H.wire(p, a, 97.79, b, 97.79)
    H.label(p, "N", 88.9, 97.79)
    R.run_case("GM-08", f"i2_pin_on_own_net_wire:{tag}", "wire_pins_to_net", p, W, script=SC,
               also=["GM-12"],
               note=f"R1:1 on the interior of a wire that carries N (drawn {a}->{b}); wire R1:1 to N")
R.end()
