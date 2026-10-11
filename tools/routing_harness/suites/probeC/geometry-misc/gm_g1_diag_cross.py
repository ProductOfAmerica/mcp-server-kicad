"""Rerun of probe-geometry-misc/g1_diag_cross.py on M2 and K (GM-01: g1C, g1D, g1E, g1F;
GM-15: g1A, g1B). A new stub or route crossing a foreign 45-degree wire; fixture code copied.
Raw control g1Ck is not re-run (no design call).
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

SC = G.begin(__file__, "probe-geometry-misc/g1_diag_cross.py")


def fixture(name, foo=None, kind="label", r4=False):
    p = H.fresh(name)
    H.place(p, "Device:R", "R1", 101.6, 101.6)
    H.place(p, "Device:R", "R2", 114.3, 97.79)
    H.wire(p, 99.06, 99.06, 104.14, 93.98)  # 45-degree, through (101.6, 96.52)
    H.wire(p, 104.14, 93.98, 114.3, 93.98)  # diagonal end -> R2:1
    if foo is not None:
        H.place(p, "Device:R", "R3", 127, 101.6)
        H.wire(p, 127, 97.79, 127, 95.25)
        H.label(p, "FOO", 127, 95.25, kind=kind)
        H.label(p, "FOO", foo[0], foo[1], kind=kind)
    if r4:
        H.place(p, "Device:R", "R4", 101.6, 86.36)  # R4:2 at (101.6, 90.17)
    return p


W = G.W(("R1", "1"))
V = "g1_diag_cross:"
R.run_case("GM-15", V + "g1A", "wire_pins_to_net", fixture("g1A"), W, script=SC,
           note="bare 45-degree crossing of the 1x stub interior")
R.run_case("GM-15", V + "g1B", "wire_pins_to_net", fixture("g1B", (101.6, 96.52)), W, script=SC,
           note="local FOO exactly at the crossing X")
R.run_case("GM-01", V + "g1C", "wire_pins_to_net", fixture("g1C", (101.6001, 96.5199)), W, script=SC,
           note="local FOO on the diagonal, 1 IU from the stub line")
R.run_case("GM-01", V + "g1D", "wire_pins_to_net", fixture("g1D", (101.6002, 96.5198)), W, script=SC,
           note="local FOO on the diagonal, 2 IU from the stub line")
R.run_case("GM-01", V + "g1E", "wire_pins_to_net",
           fixture("g1E", (101.6001, 96.5199), kind="global_label"), W, script=SC,
           note="global FOO on the diagonal, 1 IU from the stub line")
p = fixture("g1F", (101.6001, 96.5199), r4=True)
R.run_case("GM-01", V + "g1F", "connect_pins", p, G.C("R1", "1", "R4", "2"), script=SC,
           note="connect_pins R1:1 -> R4:2 (straight, crosses the diagonal next to FOO)")
R.end()
