"""Rerun of probe-geometry-misc/o2_wire_near_stub_end.py on M2 and K (GM-02; o2a-o2c also GM-10,
where P wrote a stray junction). Off-grid foreign wire next to the new stub end; fixture copied.
Raw control o2ak is not re-run (no design call).
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

SC = G.begin(__file__, "probe-geometry-misc/o2_wire_near_stub_end.py")
W = G.W(("R1", "1"))


def fixture(name, y):
    p = H.fresh(name)
    H.place(p, "Device:R", "R1", 101.6, 101.6)
    H.wire(p, 96.52, y, 106.68, y)
    H.label(p, "FOO", 106.68, y)
    H.place(p, "Device:R", "R3", 127, 101.6)
    H.wire(p, 127, 97.79, 127, 95.25)
    H.label(p, "FOO", 127, 95.25)
    return p


for tag, y, what, also in (("o2a", 95.2499, "1 IU above E1 (beyond the stub end)", ["GM-10"]),
                           ("o2b", 95.2501, "1 IU below E1 (crosses the stub 1 IU before its end)",
                            ["GM-10"]),
                           ("o2c", 95.2498, "2 IU above E1", ["GM-10"]),
                           ("o2d", 95.20, "0.05 mm above E1", [])):
    R.run_case("GM-02", f"o2_wire_near_stub_end:{tag}", "wire_pins_to_net", fixture(tag, y), W,
               script=SC, also=also, note=f"foreign FOO wire {what}")
R.end()
