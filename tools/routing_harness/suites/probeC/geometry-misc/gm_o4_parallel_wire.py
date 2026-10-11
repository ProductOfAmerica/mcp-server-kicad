"""Rerun of probe-geometry-misc/o4_parallel_wire.py on M2 and K (GM-02 o4a, also GM-10, GM-11;
GM-11 o4b; o4c, o4d to GM-02, the first case citing the script). Foreign wire parallel to the
stub; fixture copied. Raw controls o4ak1, o4ak2 are not re-run (no design call).
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

SC = G.begin(__file__, "probe-geometry-misc/o4_parallel_wire.py")
W = G.W(("R1", "1"))


def fixture(name, d):
    p = H.fresh(name)
    H.place(p, "Device:R", "R1", 101.6, 101.6)
    x = round(101.6 + d, 4)
    H.wire(p, x, 92.71, x, 100.33)
    H.label(p, "FOO", x, 92.71)
    H.place(p, "Device:R", "R3", 127, 101.6)
    H.wire(p, 127, 97.79, 127, 95.25)
    H.label(p, "FOO", 127, 95.25)
    return p


for tag, d, case, also in (("o4a", 0.0001, "GM-02", ["GM-10", "GM-11"]),
                           ("o4b", 0.05, "GM-11", []),
                           ("o4c", 0.0501, "GM-02", []),
                           ("o4d", 0.635, "GM-02", [])):
    R.run_case(case, f"o4_parallel_wire:{tag}", "wire_pins_to_net", fixture(tag, d), W, script=SC,
               also=also, note=f"foreign FOO wire parallel to the stub, {round(d * 1e4)} IU to the right")
R.end()
