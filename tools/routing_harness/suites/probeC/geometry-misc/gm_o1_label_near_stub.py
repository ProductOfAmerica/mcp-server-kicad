"""Rerun of probe-geometry-misc/o1_label_near_stub.py on M2 and K (GM-01).
Off-grid foreign label beside the new stub's interior; fixture code copied verbatim.
Raw control o1ak (stub + label N written by hand) is not re-run (no design call).
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

SC = G.begin(__file__, "probe-geometry-misc/o1_label_near_stub.py")
W = G.W(("R1", "1"))


def fixture(name, d, kind="label", r4=False):
    p = H.fresh(name)
    H.place(p, "Device:R", "R1", 101.6, 101.6)
    H.place(p, "Device:R", "R2", 114.3, 100.33)  # R2:1 at (114.3, 96.52)
    q = round(101.6 + d, 4)
    H.wire(p, 114.3, 96.52, q, 96.52)
    H.label(p, "FOO", q, 96.52, kind=kind)
    H.place(p, "Device:R", "R3", 127, 101.6)
    H.wire(p, 127, 97.79, 127, 95.25)
    H.label(p, "FOO", 127, 95.25, kind=kind)
    if r4:
        H.place(p, "Device:R", "R4", 101.6, 86.36)  # R4:2 at (101.6, 90.17)
    return p


for tag, d in (("o1a", 0.0001), ("o1b", 0.0002), ("o1c", 0.05), ("o1d", 0.0501), ("o1e", 0.635)):
    R.run_case("GM-01", f"o1_label_near_stub:{tag}", "wire_pins_to_net", fixture(tag, d), W, script=SC,
               note=f"FOO label (on a foreign wire end) {round(d * 1e4)} IU right of the stub interior")
R.run_case("GM-01", "o1_label_near_stub:o1f", "wire_pins_to_net",
           fixture("o1f", 0.0001, kind="global_label"), W, script=SC,
           note="GLOBAL FOO 1 IU right of the stub interior")
R.run_case("GM-01", "o1_label_near_stub:o1g", "connect_pins", fixture("o1g", 0.0001, r4=True),
           G.C("R1", "1", "R4", "2"), script=SC,
           note="connect_pins R1:1 -> R4:2 straight up, FOO 1 IU beside the route")
R.end()
