"""Rerun of verify-probe-geometry-misc/v14_gm14_stub_length_validation.py on M2 and K (GM-14: s0..s4).
The original ran P0, P, M and B on each fixture: one variant per stub_length here.
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

SC = G.begin(__file__, "verify-probe-geometry-misc/v14_gm14_stub_length_validation.py")

for i, L in enumerate((0, 0.00004, -2.54, -3.81, -7.62)):
    p = H.fresh(f"s{i}")
    H.place(p, "Device:R", "R1", 76.2, 76.2)
    R.run_case("GM-14", f"v14_gm14_stub_length_validation:s{i}", "wire_pins_to_net", p,
               G.W(("R1", "2"), stub_length=L), script=SC, note=f"stub_length={L}")
R.end()
