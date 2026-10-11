"""Rerun of verify-probe-geometry-misc/v03_gm03_nongrid_stub.py on M2 and K (GM-03: L0..L6, one per
stub_length). The printed lattice-distance table makes no call and is not re-run.
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

SC = G.begin(__file__, "verify-probe-geometry-misc/v03_gm03_nongrid_stub.py")


def fixture(name):
    p = H.fresh(name)
    H.place(p, "Device:R", "R1", 76.2, 76.2)
    H.place(p, "Device:R", "R3", 101.6, 76.2)
    H.wire(p, 101.6, 72.39, 101.6, 69.85)
    H.label(p, "FOO", 101.6, 69.85)
    H.wire(p, 71.12, 82.55, 81.28, 82.55)
    H.label(p, "FOO", 71.12, 82.55)
    return p


for i, L in enumerate((2.54, 0.1 * 25.4, 2.5399, 2.5401, 2.5, 2.0, 3.0)):
    R.run_case("GM-03", f"v03_gm03_nongrid_stub:L{i}", "wire_pins_to_net", fixture(f"L{i}"),
               G.W(("R1", "2"), stub_length=L), script=SC,
               note=f"stub_length={L!r} (IU {G.iu(L)}), lattice FOO wire across the axis at y=82.55")
R.end()
