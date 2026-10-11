"""Rerun of probe-geometry-misc/s2_zero_negative.py on M2 and K (GM-14). Zero, negative and sub-IU
stub_length; the original ran P, P0, M and B on each fixture: one variant per stub_length here.
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

SC = G.begin(__file__, "probe-geometry-misc/s2_zero_negative.py")

for tag, L in (("s2a", 0), ("s2b", -2.54), ("s2c", -7.62), ("s2d", 0.00004), ("s2e", -3.81)):
    p = H.fresh(tag)
    H.place(p, "Device:R", "R1", 101.6, 101.6)
    R.run_case("GM-14", f"s2_zero_negative:{tag}", "wire_pins_to_net", p,
               G.W(("R1", "1"), stub_length=L), script=SC,
               note=f"R1:1 auto direction, stub_length {L}")
R.end()
